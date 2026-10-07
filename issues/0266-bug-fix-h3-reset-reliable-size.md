# h3 でストリームを開いた直後にリセットすると Reliable Size が WT ヘッダー長未満になる

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-reset-reliable-size

## 目的

draft-ietf-webtrans-http3-16 Section 4.4 の MUST「WebTransport implementations MUST use the RESET_STREAM_AT frame with a Reliable Size set to at least the size of the WebTransport header when resetting a WebTransport data stream.」を満たす。現状は、ストリームを開いた直後 (1 バイトも送っていない状態) にリセットすると Reliable Size が 0 になり、ピアがセッション ID を復元できない。

## 現状

- `src/webtransport/h3/client.py` の `Client.open_stream` は `h3.Session.open_stream` でストリームを登録するだけで、WT ヘッダー (単方向 0x54 / 双方向 0x41 + セッション ID) の QUIC への書き出しは送信ループに委ねる。`send_stream_data` と違い `_send_pending` を呼ばない
- `src/webtransport/h3/server.py` の `Server.open_stream` も同様に `_send_to` を呼ばず、docstring に「ストリームは送信するまでクライアントに認識されない」と明記している
- `Client.reset_stream` / `Server.reset_stream` は `quic_connection.reset_stream` を `webtransport_session.reset_stream` より先に呼ぶ。この時点で ngtcp2 のストリーム書き込みオフセットは 0 のため、`QuicConnection::reset_stream` の `NGTCP2_SHUT_STREAM_FLAG_FLUSH` は Reliable Size 0 の RESET_STREAM (RESET_STREAM_AT と等価) を送出する
- `tests/test_e2e_webtransport_h3_low_level.py` の `test_stream_reset_before_data_received_minus_one` が `info.reset_session_id == -1` を固定しており、「ヘッダー未送信のままリセットするとピアがセッション ID を復元できない」挙動がテストで記録されている
- 0025 (closed) は `QuicConnection::reset_stream` に RESET_STREAM_AT の送出を実装したが、本件は「リセット時点でヘッダーが未書き込み」という別の穴である
- 影響: 開いてすぐ中断したストリームがピア側でセッションに紐づかず、ピアのセッション単位の簿記 (ストリーム数やフロー制御の整合) と食い違う。仕様が Reliable Size で保証しようとした ID フィールドの確実な配信が成立しない

## 設計方針

- リセット時に WT ヘッダーが確実に届く状態にする。候補は 2 つで、実装時に既存挙動への影響を測って選ぶ
  - `h3.Client.open_stream` / `h3.Server.open_stream` で `_send_pending` / `_send_to` を呼び、開設時点でヘッダーを QUIC へ書き出す (nghttp3 がヘッダーを生成するタイミングを確認し、戻る時点で書き込みオフセットがヘッダー長以上になっていることを保証する)
  - `reset_stream` で、対象ストリームの書き込みオフセットがヘッダー長未満の場合にヘッダーを先に送出してからリセットする
- どちらの案でも、ピアが `reset_stream_at` transport parameter を広告しない場合は ngtcp2 が通常の RESET_STREAM を送るため本 MUST は満たせない。既存の緩和 (0093) と同じ制約としてテストの前提に明記する
- `h3.Server.open_stream` の docstring「ストリームは送信するまでクライアントに認識されない」は、案 1 を採る場合は記述を更新する
- 変更対象: `src/webtransport/h3/client.py` / `server.py`、`tests/test_e2e_webtransport_h3_low_level.py`、必要なら `src/bindings/webtransport_h3.cpp` (ヘッダー書き出しのタイミング)、`skills/webtransport-py/SKILL.md`

## 完了条件

- ストリームを開いた直後 (データ送信なし) にリセットしても、ピア側でセッション ID が復元できる。`on_stream_reset` に渡る session_id が -1 にならない
- ピアが `reset_stream_at` を広告しない場合の既存挙動 (通常の RESET_STREAM、session_id -1) は変わらない
- データを送信してからリセットする既存経路の挙動 (Reliable Size が送信済みオフセット、`tests/test_e2e_webtransport_h3_low_level.py` の `test_stream_reset_at_recovers_session_id`) が変わらない
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する

## 解決方法
