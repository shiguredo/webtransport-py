# 高レベル QUIC クライアントに shutdown_stream と wait_for_stream_reset を追加する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-stream-abort
- Polished: YYYY-MM-DD
- Reporter: @voluntas

## 目的

高レベル `Client` に ngtcp2-py 互換の `shutdown_stream` と `wait_for_stream_reset` を追加し、ストリーム中断系のテストを webtransport-py に置き換えられるようにする。

## 現状

- webtransport-py の高レベル `Client` にはストリーム送受信の中断 API が無い
- 低レベル `Connection` には `close_stream` (RESET_STREAM + STOP_SENDING を送出) / `stop_sending` / `reset_stream` があり、`EventType.STREAM_RESET` イベントも存在するが、高レベル `Client` の `run()` は `STREAM_RESET` イベントを処理していない
- sora-quic のテスト (`test_ngtcp2_stop_sending_triggers_reset_stream` / `test_ngtcp2_reset_stream_notifies_controller` / `test_ngtcp2_connection_survives_stream_abort`) が `shutdown_stream` + `wait_for_stream_reset` を組み合わせて使用する

## 設計方針

- `shutdown_stream(stream_id, error_code=0)`: 低レベル `close_stream` を呼び、RESET_STREAM (RFC 9000 Section 19.4) と STOP_SENDING (RFC 9000 Section 19.5) の両方を送出する
- `wait_for_stream_reset(stream_id, timeout=10.0) -> int`: ピアの RESET_STREAM 受信を待ち、そのアプリケーションエラーコードを返す。`run()` のループで `STREAM_RESET` イベントを処理し、ストリームごとのエラーコードを保持して待機 API に公開する。期限までに受信しない場合は `TimeoutError` を raise する
- 受信した `STREAM_RESET` はストリームの受信状態 (recv_stream_data 側) にも影響するため、他 API との整合を取りながら最小限の状態管理を追加する

## 完了条件

- `shutdown_stream` で双方向ストリームの RESET_STREAM と STOP_SENDING を送出できる
- `wait_for_stream_reset` がピアの RESET_STREAM が運んだエラーコードを返す
- 期限までに RESET_STREAM を受信しない場合は `TimeoutError` を raise する
- 中断したストリームとは別のストリームでデータ転送が継続できる
- テストを追加する (エラーコードの複製 / タイムアウト / ストリーム中断後の接続生存)

## 解決方法

(実装時に追記する)