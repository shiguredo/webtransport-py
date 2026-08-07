# 高レベル QUIC Client にバックグラウンド受信タスクを導入する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-background-recv-task
- Polished: YYYY-MM-DD
- Reporter: @voluntas

## 目的

高レベル `Client` が `run()` を明示起動しなくても受信イベントを処理できるようにし、sora-quic のテスト置き換え (ngtcp2-py から webtransport-py へ) で使われる `recv_stream_data` (0037) と `wait_for_stream_reset` (0038) の前提を用意する。

## 現状

- webtransport-py の高レベル `Client` はバックグラウンドタスクを持たず、`connect()` はハンドシェイク完了で return する。受信イベント (`STREAM_DATA` / `DATAGRAM` 等) を処理するのは `run()` のみで、`run()` は接続終了までブロックする (明示的に `asyncio.create_task(client.run())` で起動する必要がある)
- sora-quic のテストは `run()` を呼ばず `connect()` → `send_stream_data()` → `recv_stream_data()` の形で使用するため、このまま置き換えると受信イベントが処理されず `recv_stream_data` は永遠にデータを受け取れない
- ngtcp2-py は `connect()` が service task + socket reader task を起動し、`recv_stream_data` / `wait_for_stream_reset` はそのタスクが供給するイベントを待つ構造である

## 設計方針

- ngtcp2-py の service task 構造を参考に、`Client` に受信イベントを処理するバックグラウンドタスクを導入する (socket の読み取り、イベントの取り込み、`recv_stream_data` / `wait_for_stream_reset` への状態反映)
- 接続終了 (CONNECTION_CLOSED) を検知した場合、待機中の `recv_stream_data` (0037) / `wait_for_stream_reset` (0038) を起床して通知する (待機側は ngtcp2-py と同じく `TimeoutError` を raise する)。タスク異常終了の場合は、ngtcp2-py の `_raise_service_task_error` と同じく元の例外を待機側に伝播する
- 既存の `run()` との関係を明確にする。既存テストは `asyncio.create_task(client.run())` で受信を処理しているため、バックグラウンドタスクと `run()` の二重受信・競合を設計上排除する (排他制御または `run()` の役割変更)
- 既存の `on_stream_data` / `on_datagram` / `on_connection_closed` コールバックの挙動は維持する

## 完了条件

- `run()` を明示起動しなくても受信イベントが処理され、ストリームごとの受信状態が更新される
- 既存の `run()` ベースのテスト (`create_task(client.run())` パターン) が引き続き通る
- バックグラウンドタスクと `run()` が同時に動作しても受信イベントの取りこぼしや二重処理が発生しない
- `recv_stream_data` (0037) と `wait_for_stream_reset` (0038) が動作することは各 issue で検証する

## 解決方法

(実装時に追記する)