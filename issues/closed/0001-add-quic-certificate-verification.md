# QUIC クライアントの証明書検証を実装する

- Priority: High
- Created: 2026-07-25
- Completed: 2026-07-26
- Model: Composer
- Branch: feature/add-quic-certificate-verification
- Polished: YYYY-MM-DD

## 目的

README が掲げる「証明書のカスタム検証」を実装し、`verify_peer=True` のときに実際にピア証明書を検証できるようにする。現状は検証オフ切替しかなく、本番利用で危険な状態になっている。

## 優先度根拠

- README に記載があるが未実装（過大広告）
- `verify_peer=True` でも trust store・ホスト名検証がないため、セキュリティ上の欠陥
- 0-RTT / Migration より先に直すべき基盤

## 現状

[`src/bindings/quic.cpp`](src/bindings/quic.cpp) の `create_ssl_ctx()` は、クライアントかつ `verify_peer=False` のときだけ `SSL_VERIFY_NONE` を設定する。`verify_peer=True` でも `SSL_VERIFY_PEER`・既定 CA・`ca_file`・カスタムコールバック・ホスト名検証はない。公開 API は `QuicConfig.verify_peer` と asyncio `Client(verify_peer=...)` のみ。

## 設計方針

ngtcp2 の BoringSSL client example（`tls_client_context_boringssl.cc`）に合わせる。

- `verify_peer=True` 時は `SSL_VERIFY_PEER` + 既定 verify paths（または `ca_file`）
- SNI（`server_name`）に基づくホスト名検証
- カスタム検証コールバックを nanobind 経由で渡せるようにする（ピア証明書 DER）
- HTTP/2 系 TLS は本 issue の対象外（QUIC 系を先に完了）

## 完了条件

- 自己署名証明書 + `verify_peer=True` で握手が失敗する
- 同じ証明書を `ca_file` に渡すと握手が成功する
- カスタムコールバックで許可 / 拒否ができる
- モックなしの e2e / 統合テストがある
- asyncio QUIC / HTTP3 / H3 クライアントから `ca_file` とカスタム検証を使える

## 解決方法

コミット 6792805 で実装した。

- `QuicConfig` に `ca_file` / `verify_callback` を追加する（`src/bindings/quic.h` の `QuicConfig`）
- `create_ssl_ctx()` で `verify_peer=True` 時に `SSL_VERIFY_PEER` + `ca_file` / 既定 verify paths を設定する
- `setup_client_session()` でカスタム検証コールバック（`SSL_set_custom_verify`）と SNI ベースのホスト名検証（`SSL_set1_host` / `X509_VERIFY_PARAM_set1_ip_asc`）を適用する
- asyncio QUIC / HTTP3 / H3 クライアントに `ca_file` / `verify_callback` を配線する
- `tests/test_e2e_quic_advanced.py` に証明書検証の e2e テストを追加する（自己署名拒否 / `ca_file` 成功 / カスタムコールバック許可・拒否）
