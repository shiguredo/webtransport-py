# QUIC バインディングがピアからの STOP_SENDING をアプリに一切通知しない

- Created: 2026-09-06
- Completed: 2026-09-12
- Branch: feature/add-quic-stop-sending-event-propagation
- Polished: {YYYY-MM-DD}

## 目的

ngtcp2 はピアからの STOP_SENDING 受信通知として `recv_stop_sending` と `stream_stop_sending` の 2 コールバックを提供するが、QUIC バインディングはいずれも登録していない。`QuicEventType` にも `StopSending` が無い。RESET_STREAM は `on_stream_reset` で伝播するが STOP_SENDING はアプリに一切届かない。draft-ietf-webtrans-http3-16 Section 4.4 冒頭「A WebTransport endpoint can send a RESET_STREAM or a STOP_SENDING frame for a WebTransport data stream. Those signals are propagated by the WebTransport implementation to the application」の仕様違反。対向が送信停止を要求してもアプリが検知できず、エラーコードも受け取れない。

## 現状

- `src/bindings/quic.cpp` の 3 つの `initialize_client` / `initialize_server` / `initialize_server_from_packet` の callbacks 設定で `callbacks.recv_stop_sending` と `callbacks.stream_stop_sending` の設定は 0 件 (grep 済み)
- 設定しているコールバックは `client_initial` / `recv_crypto_data` / `encrypt` / `decrypt` / `hp_mask` / `recv_stream_data` / `acked_stream_data_offset` / `stream_open` / `stream_close` / `stream_reset` / `recv_datagram` / `handshake_completed` / `rand` / `get_new_connection_id` / `update_key` / `recv_retry` / `delete_crypto_aead_ctx` / `delete_crypto_cipher_ctx` / `get_path_challenge_data` / `version_negotiation` / `path_validation` / `tls_early_data_rejected` のみ
- `src/bindings/quic.h` の `QuicEventType` に `StopSending` が無い
- `_deps/ngtcp2/reliable-stream-reset/source/lib/includes/ngtcp2/ngtcp2.h` の 3495 行に `stream_stop_sending`、3513 行に `recv_stop_sending` のシグネチャが定義されている
- 高レベル `h3.Client` / `h3.Server` / `quic.Client` / `quic.Server` の `on_*` コールバックにも `on_stop_sending` は無い

## 設計方針

- `QuicConnection` の 3 つの初期化経路で `callbacks.recv_stop_sending = recv_stop_sending_cb` を登録する (ngtcp2 のドキュメント上、`stream_stop_sending` は非推奨で `recv_stop_sending` が新 API)
- `QuicEventType::StopSending` を追加する (下位互換を維持しない CODEBASE 方針に従い、enum の順序も見直す)
- `QuicEvent` に `error_code` (既存) と `stream_id` (既存) を活用してイベントを push する
- 高レベル `quic.Client` / `quic.Server` に `on_stop_sending` コールバックを追加する
- h3 / http3 の高レベル層は既に `webtransport_h3.cpp` / `http3.cpp` で STOP_SENDING イベントを扱っているため、QUIC 層の追加と整合させる
- issue 0130 (HTTP/3 プロトコルエラーのエラーコード通知 API) と関連するが本 issue は QUIC 層の STOP_SENDING 伝播に限定する

## 完了条件

- QUIC ピアが STOP_SENDING を送ると `on_stop_sending(stream_id, error_code)` (仮) が発火すること
- エラーコードがアプリに配信されること
- `tests/` に STOP_SENDING の伝播テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること

## 解決方法

QUIC バインディングにピアからの STOP_SENDING の受信と伝播を実装した。

- `src/bindings/quic.h` の `QuicEventType` に `StopSending` を追加し、`recv_stop_sending_cb` を宣言した
- `src/bindings/quic.cpp` の `initialize_client` / `initialize_server` / `initialize_server_from_packet` の 3 経路で `callbacks.recv_stop_sending` を登録した。`recv_stop_sending_cb` は `stream_id` とアプリケーションエラーコードを持つ `StopSending` イベントを push する。`stream_stop_sending` はローカル側の送信停止要求であり受信通知ではないため使わない
- Python 側の `quic.EventType` に `STOP_SENDING` を追加した
- 高レベル `quic.Client` に `on_stop_sending(stream_id, error_code)`、`quic.Server` に `on_stop_sending(stream_id, error_code, addr)` を追加し、受信イベントから発火するようにした (h3 / http3 の高レベル層が既に持つ `on_stream_reset` と対称)
- `skills/webtransport-py/SKILL.md` のコールバック一覧と `quic.EventType` 一覧を更新した
- テストを 3 本追加した。`test_stop_sending_event_propagates_error_code` と `test_stop_sending_event_exposes_stream_id` は Sans-IO 層で `close_stream` により STOP_SENDING を実際に送出させ、イベントの `stream_id` と `error_code` を検証する。`test_high_level_server_on_stop_sending` は高レベル Server の `on_stop_sending` が発火しエラーコードが配信されることを検証する

`callbacks.recv_stop_sending` の登録を外すと 3 テストとも失敗することを確認し、テストが実際に伝播経路を検証していることを確かめた。`uv run pytest tests/ --timeout=30` の 1072 件が全て通る。
