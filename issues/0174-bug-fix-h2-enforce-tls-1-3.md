# WebTransport over HTTP/2 の TLS 要件 (draft-15 Section 7) を強制し既定を TLS 1.3 のみにする

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-enforce-tls-1-3
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 7 は「Clients MUST NOT send WebTransport over HTTP/2 requests on connections that do not meet one of the two conditions: TLS 1.3、または TLS 1.2 + extended master secret」を求める。本実装の `h2.Client` / `h2.Server` は Python の `ssl.create_default_context()` を使うため既定で TLS 1.2 を許容する。実験で TLS 1.2 のみのサーバーへ CONNECT を送出できてしまい、仕様違反かつ Python の `ssl` は EMS 交渉の有無を公開しないため TLS 1.2 では条件を検証できない。既定を TLS 1.3 のみに絞る。

## 現状

- `src/webtransport/h2/client.py` の `Client.connect` は `ssl_context = ssl.create_default_context()` (既定で `PROTOCOL_TLS_CLIENT`、`minimum_version = TLSv1_2`)
- `src/webtransport/h2/server.py` の `Server.start` は `ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` (既定で `minimum_version = TLSv1_2`)
- 実験: TLS 1.2 のみのサーバー (`ssl.SSLContext(PROTOCOL_TLS_SERVER); ctx.maximum_version = TLSv1_2`) に対し `h2.Client` は接続し CONNECT を送出 (TLSv1.2 / ECDHE-ECDSA-AES256-GCM-SHA384)
- draft-15 Section 7 の MUST
- Python の `ssl` は `SSL_get_extms_support` に相当する API を公開しない (`ssl.SSLObject.session` / `SSLObject.compression()` / `cipher()` はある)
- RFC 9113 Section 9.2 (HTTP/2 の TLS 1.2 制約) は refs に無いが同種の制約を持つ

## 設計方針

- `h2.Client` / `h2.Server` の SSLContext に `ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3` を明示的に設定する
- README / SKILL.md に「TLS 1.3 のみサポート」を明記する (draft-15 Section 7 準拠を目的とする旨も添える)
- 例外: 将来 TLS 1.2 + EMS の検証手段が Python で公開された場合に緩和を検討する余地を残す (現時点では TLS 1.3 のみで簡明)
- `http2.Client` / `http2.Server` (WebTransport ではない HTTP/2) は本 issue の範囲外 (draft-15 の対象は WebTransport のみ)

## 完了条件

- TLS 1.2 のみのサーバーへの `h2.Client.connect` が `HandshakeFailedError` で拒否されること
- TLS 1.2 のみのクライアントからの `h2.Server` 接続が拒否されること
- TLS 1.3 での接続は従来どおり成功すること
- README / SKILL.md の TLS 節が更新されていること
- `tests/` に TLS バージョン拒否テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
