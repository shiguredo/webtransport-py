# WebTransport over HTTP/2 のストリーム ID の initiator ビットと方向を検証せず ID 衝突で状態が壊れる

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-stream-id-initiator-validation
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/2 の実装は WT ストリーム ID の initiator ビット (bit 0) と方向性 (bit 1) を検証しない。ピアが本実装側の initiator を持つ stream_id で WT_STREAM を送ってきても新規エントリとして受け入れ、`open_stream` が同じ ID を後で払い出すと既存エントリが上書きされる。QUIC なら WT_STREAM_STATE_ERROR で拒否すべき経路。draft-ietf-webtrans-http2-15 Section 5.2 のストリーム状態は RFC 9000 Section 3 のミラーであり、initiator / 方向違反は仕様違反。ピアの単方向ストリームに送信できてしまう / 送信専用ストリームへの STOP_SENDING を受理する経路も同根。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_stream` は未知 stream_id を常に `is_local = false` で新規作成 (initiator 未検証)
- `H2Session::open_stream` は既存エントリを上書き (`wt_session->streams[stream_id] = info`)
- `H2Session::send_stream_data` は receive 側の単方向 (クライアント起点 %4==2 / サーバー起点 %4==3) への送信を塞がない
- `H2Session::handle_wt_stop_sending` / `handle_wt_reset_stream` は送信専用ストリームへの受信を受理してしまう
- 実験:
  - (a) クライアントがサーバー initiator の ID 1 で WT_STREAM を送るとサーバーは受理し、その後サーバーの `open_stream` が同じ ID 1 を返す (衝突・上書き)
  - (b) クライアント自身の単方向 (送信専用) ストリーム ID 2 にサーバーからデータが来ると `StreamData` として配送
  - (c) サーバーがピアの単方向ストリーム 2 に `send_stream_data` すると WT_STREAM がワイヤに出る
  - (d) 送信専用ストリームへの WT_STOP_SENDING を `StopSending` イベントとして受理
- `incoming_stream_exceeds_limit` の `(stream_id >> 2) + 1` 計算そのものは 4 種別共通の正しい計算 (initiator ビットとは独立)

## 設計方針

- `handle_wt_stream` / `handle_wt_reset_stream` / `handle_wt_stop_sending` / `handle_wt_max_stream_data` / `handle_wt_streams_blocked` の各受信ハンドラで、stream_id の initiator ビットと自側ロールを照合する。ピアの initiator と一致しない ID は WT_STREAM_STATE_ERROR で拒否
- 単方向ストリームは方向をチェックする。送信専用 (自側 initiator + uni) の受信、受信専用 (ピア initiator + uni) への送信をそれぞれ WT_STREAM_STATE_ERROR で拒否
- `open_stream` は既存エントリの上書きを禁止 (エントリが既にあれば -1 を返す)
- `stream_id % 4` の値と (`is_server_`, `is_uni`, `is_local`) の対応表を関数化 (`is_valid_wt_stream_id` 等) して集約する

## 完了条件

- ピア initiator の ID で送られた WT_STREAM が WT_STREAM_STATE_ERROR で拒否されること
- 受信専用ストリームへの `send_stream_data` が黙って無視される (または assert / エラー) こと
- 送信専用ストリームへの WT_STOP_SENDING が WT_STREAM_STATE_ERROR で拒否されること
- 既存の受理ロジック (双方向 / 自側単方向) が引き続き動作すること
- `tests/` に上記 4 経路の回帰テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
