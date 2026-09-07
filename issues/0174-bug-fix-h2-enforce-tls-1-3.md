# WebTransport over HTTP/2 の TLS 要件 (draft-15 Section 7) を強制し既定を TLS 1.3 のみにする

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-enforce-tls-1-3
- Polished: 2026-09-07

## 目的

draft-ietf-webtrans-http2-15 Section 7 は「Clients MUST NOT send WebTransport over HTTP/2 requests on connections that do not meet one of the two conditions: TLS 1.3、または TLS 1.2 + extended master secret」を求める。本実装の `h2.Client` は `verify_peer` 真偽で 2 分岐の SSLContext 生成をし、`h2.Server` は `PROTOCOL_TLS_SERVER` を使い、いずれも既定 `minimum_version` は TLS 1.2 のままである (実行で確認済み)。実験で TLS 1.2 のみのサーバーへ CONNECT を送出できてしまい、仕様の 2 条件を満たさない接続を使えてしまう。Python の `ssl` は EMS 交渉の有無を公開しないため TLS 1.2 では条件を検証できず、TLS 1.2 + EMS の合法接続も現時点では拒否する。既定を TLS 1.3 のみに絞る。

## 現状

- `src/webtransport/h2/client.py` の `Client.connect` は `verify_peer` 真で `ssl.create_default_context()`、偽で `ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` を使う (いずれも既定 `minimum_version` は TLS 1.2。実行で確認済み)
- `src/webtransport/h2/server.py` の `Server.start` は `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` を使う (既定 `minimum_version` は TLS 1.2。実行で確認済み)
- 実験手順: TLS 1.2 のみのサーバー (`ssl.SSLContext(PROTOCOL_TLS_SERVER)` に `maximum_version = TLSv1_2` を設定し自己署名証明書を載せる) に対し、既定設定の `h2.Client` で `connect()` を試行する。現状は TLS 1.2 でハンドシェイクが成立し CONNECT を送出する方向であり、`minimum_version` 設定が皆無 (grep で 0 件) のため阻止経路がない。実行記録は未取得のため、実装時に再測定する
- draft-15 Section 7 の MUST
- Python の `ssl` は `SSL_get_extms_support` に相当する API を公開しない (`ssl.SSLObject.session` / `SSLObject.compression()` / `cipher()` はある。実行で確認済み)
- RFC 9113 Section 9.2 (HTTP/2 の TLS 1.2 制約) は refs に無いが同種の制約を持つ (変更対象には含めない調査メモ)

## 設計方針

- `h2.Client` の 2 分岐と `h2.Server` の生成箇所のすべてに `ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3` を明示的に設定する
- README の `### WebTransport over HTTP/2` 直下と SKILL.md の `### WebTransport over HTTP/2 (webtransport.h2)` 直下に「TLS 1.3 のみサポート (draft-15 Section 7 準拠のため。TLS 1.2 + EMS の合法接続も現時点では拒否する)」の注意書きを新設する
- 例外: 将来 TLS 1.2 + EMS の検証手段が Python で公開された場合の緩和は本 issue の対象外とし、必要になれば別途起票する
- `http2.Client` / `http2.Server` (WebTransport ではない HTTP/2) は本 issue の範囲外 (draft-15 の対象は WebTransport のみ)

## 完了条件

- TLS 1.2 のみのサーバーへの `h2.Client.connect` が `HandshakeFailedError` で拒否されること
- TLS 1.2 のみのクライアントからの接続で `h2.Server` の `_handle_client` が起動しないこと (サーバー側 TLS 層で拒否されるため。到達カウンタ不変で確認する)
- TLS 1.2 + EMS の合法接続も現時点では拒否されること (stricter-than-spec である)
- TLS 1.3 での接続は従来どおり成功すること
- README / SKILL.md の上記箇所に注意書きが新設されていること
- `tests/test_h2_tls_version.py` を新規作成し、e2e 形式 (実 Server + 実 Client、日本語コメント付き) で TLS 1.2 拒否と TLS 1.3 成功を検証すること
- 既存のテスト全 834 件が引き続き通過すること
