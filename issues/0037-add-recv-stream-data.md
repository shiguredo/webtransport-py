# 高レベル QUIC クライアントに recv_stream_data を追加する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-recv-stream-data
- Polished: 2026-08-07
- Reporter: @voluntas

## 目的

高レベル `Client` に ngtcp2-py 互換の `recv_stream_data` を追加し、request-response 型テストを webtransport-py に置き換えられるようにする。テストの大半がこの API を使うため最も使用頻度が高い機能である。

## 現状

- webtransport-py の高レベル `Client` にはストリームデータを FIN まで待って受信する API が無く、`on_stream_data` コールバック型のみ提供されている
- ngtcp2-py は `recv_stream_data(stream_id, timeout=10.0, *, overall_timeout=None) -> tuple[bytes, bool]` を提供しており、sora-quic の各テストが `data, fin = await client.recv_stream_data(stream_id, timeout=...)` の形で使用する
- 高レベル `Client` は `connect()` がハンドシェイク完了で return し、`run()` を明示起動しないと受信イベントを処理しない。sora-quic のテストは `run()` を呼ばないため、バックグラウンド受信タスク (0042) が前提になる
- 低レベル binding は受信データのフロー制御前進 (0035) が必要であり、別 issue で対応する

## 設計方針

- ngtcp2.h は `recv_stream_data_cb` にデータを offset の非減少順・重複なしで渡すことを保証し、実装上は gap なしで連続配送する (ngtcp2_conn.c の `conn_emit_pending_stream_data` の実装挙動)。そのため reorder 再構成 (gap 検出 / 重複セグメントのマージ / final size の整合性検証) は実装しない
- 変更対象は `src/webtransport/quic/client.py` の高レベル `Client`。ストリームごとの受信状態として、受信データの累積連結と FIN 検出・完了判定のみを管理し、受信状態の完了を待機側へ通知するイベントを保持する
- 受信イベントの処理はバックグラウンド受信タスク (0042) が行い、`recv_stream_data` はそのストリーム受信状態を待つ。`connect()` 後に `run()` を明示起動しなくても動作する
- 2 段構えのタイムアウト: 進捗 (待機中のストリームの STREAM_DATA イベント受信) があるたびに延びる idle deadline (`timeout`) と、進捗に関係なく動かない absolute deadline (`overall_timeout`。None なら `max(timeout * 6, 30)` を使う)
- FIN を受信したら `(bytes, fin)` を返す。呼び出し時点で既に FIN 完了済みのストリームは即時 return する (sora-quic のテストは全ストリーム送信後に順次 recv する使い方のため)。正常 return では fin は常に True (期限到達時は `TimeoutError` を raise するため)。ゼロ長 FIN (datalen=0, fin=True) も完了として扱う。FIN と期限の検出が同時になった場合は FIN を優先する (ngtcp2-py と同じ)
- どちらかの期限に達したら、受信済みバイト数と timeout 値を含むメッセージで `TimeoutError` を raise する (ngtcp2-py と同じ形式)。接続終了 (CONNECTION_CLOSED) を受信した場合も待機を終了し、`TimeoutError` を raise する。接続終了・タスク異常終了の待機者への起床はバックグラウンド受信タスク (0042) が担う
- 待機中に STREAM_RESET を受信した場合の挙動の追加定義は 0038 (`shutdown_stream` / `wait_for_stream_reset` 追加) に委ねる。0037 単体の実装では、ngtcp2-py と同じく、STREAM_RESET 受信時は待機中のストリームの進捗として idle deadline を延長し、`overall_timeout` まで待って `TimeoutError` を raise する
- 既存の `on_stream_data` コールバックは従来どおり発火させる (受信イベントをチャンク単位の生データと fin で配信する現行挙動を維持する)。コールバックと `recv_stream_data` は独立に動作し、併用してもデータは両方に配信される。コールバック内から `recv_stream_data` を呼び出すと受信処理が進まないため、ngtcp2-py と同じく `RuntimeError` を raise する
- gap なしの連続配送は ngtcp2 の実装挙動に依存するため、ngtcp2 のバージョン更新時に対象の配送保証を再確認する

## 完了条件

- `recv_stream_data` が FIN までデータを受信し `(data, fin)` を返す (複数 STREAM_DATA イベントの累積連結を含む)
- 呼び出し時点で FIN 完了済みのストリームは即時 return する
- 待機中に進捗が無く idle deadline に達した場合と `overall_timeout` に達した場合に `TimeoutError` を raise する
- テストを追加する (正常系 / 複数チャンクの累積連結 / ゼロ長 FIN / 接続終了からの `TimeoutError` / idle タイムアウト / overall_timeout)。テストは 0042 (バックグラウンド受信タスク) の実装後に実施する
- 既存の全テストが通る

## 解決方法

(実装時に追記する)