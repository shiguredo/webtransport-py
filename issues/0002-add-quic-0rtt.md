# QUIC の Session ticket と 0-RTT を実装する

- Priority: High
- Created: 2026-07-25
- Completed: YYYY-MM-DD
- Model: Composer
- Branch: feature/add-quic-0rtt
- Polished: YYYY-MM-DD

## 目的

README が掲げる「0-RTT / Session Resumption」を実装する。0-RTT に必要な Session ticket の保存・復元を含め、QUIC 層で early data を送受信できるようにする。

## 優先度根拠

- README に記載があるが未実装
- 再接続レイテンシを減らす代表機能であり、証明書検証の次に優先する

## 現状

[`src/bindings/quic.cpp`](src/bindings/quic.cpp) に ticket / early data / 0-RTT transport params の痕跡がない。`SSL_CTX_sess_set_new_cb`・`SSL_set_session`・`ngtcp2_conn_encode_0rtt_transport_params2`・`tls_early_data_rejected` いずれも未配線。

## 設計方針

ngtcp2 + BoringSSL/AWS-LC example に合わせる。

- Client: ticket の export/import、early-data capable なら early data 有効化、0-RTT TP の encode/decode
- Server: early data 有効化と `SSL_set_quic_early_data_context`
- 完了条件は **QUIC 層**（ストリームまたは DATAGRAM）の 0-RTT。WebTransport セッションの 0-RTT 確立は本 issue では広げない
- Resumption（ticket）は 0-RTT の前提として同一 issue 内で実装する

## 完了条件

- 初回接続後に Session ticket を取得できる
- ticket を使った再接続で 0-RTT（early data）を試行できる
- early data 受理 / 拒否をイベントまたは API で観測できる
- モックなしの e2e（2 回接続）がある

## 解決方法

- `QuicConfig.enable_early_data` を追加する
- `SESSION_TICKET` / `EARLY_DATA_REJECTED` イベント、または export/import API を追加する
- クライアント・サーバー双方の SSL / ngtcp2 コールバックを配線する
- asyncio `quic.Client` から ticket を扱えるようにする
- 再接続 e2e を追加する
