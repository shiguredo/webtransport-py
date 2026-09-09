# WebTransport over HTTP/2 の TLS 要件 (draft-15 Section 7) を強制し既定を TLS 1.3 のみにする

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-enforce-tls-1-3
- Polished: 2026-09-09

## 目的

draft-ietf-webtrans-http2-15 Section 7 は、クライアントに「TLS 1.3 以上、または TLS 1.2 + extended master secret (EMS)」を満たす接続でのみ WebTransport over HTTP/2 を送ることを MUST NOT で求め、サーバーには両条件を満たさない接続のリクエストを malformed として扱うことを MUST で求める。本実装の `h2.Client` は `verify_peer` 真偽で 2 分岐の SSLContext 生成をし、`h2.Server` は `PROTOCOL_TLS_SERVER` を使い、いずれも既定 `minimum_version` は TLS 1.2 のままである (実行で確認済み)。`minimum_version` の設定が皆無 (grep で 0 件) のため、TLS 1.2 接続を拒否する経路がない。Python の `ssl` は EMS 交渉の有無を公開しないため TLS 1.2 では条件を検証できず、TLS 1.2 + EMS の合法接続も現時点では拒否する。既定を TLS 1.3 のみに絞る。

## 現状

- `src/webtransport/h2/client.py` の `Client.connect` は `verify_peer` 真で `ssl.create_default_context()`、偽で `ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)` を使う (いずれも既定 `minimum_version` は TLS 1.2。実行で確認済み)
- `src/webtransport/h2/server.py` の `Server.start` は `ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)` を使う (既定 `minimum_version` は TLS 1.2。実行で確認済み)
- `minimum_version` の設定が皆無 (grep で 0 件) のため、TLS 1.2 接続を拒否する経路がない。TLS 1.2 での接続成功と CONNECT 送出の実行記録は未取得のため実装時に再測定する。再測定する場合は `verify_peer=False` で証明書検証を外し、対向を SETTINGS で `WT_ENABLED` / `ENABLE_CONNECT_PROTOCOL` を広告する HTTP/2 サーバーにしないと `_wait_webtransport_ready` の待機で止まり CONNECT に到達しない
- draft-15 Section 7 (refs 1476-1492 行): クライアントの MUST NOT と「If a server receives a WebTransport over HTTP/2 request on a connection that meets neither, the server MUST treat the request as malformed」の両方
- Python の `ssl` は `SSL_get_extms_support` に相当する API を公開しない (`ssl.SSLObject.session` / `SSLObject.compression()` / `cipher()` はある。実行で確認済み)
- RFC 9113 Section 9.2 (HTTP/2 の TLS 1.2 制約) は refs に無いが同種の制約を持つ (変更対象には含めない調査メモ)

## 設計方針

- `h2.Client` の 2 分岐と `h2.Server` の生成箇所のすべてに `ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3` を明示的に設定する
- README の `### WebTransport over HTTP/2` 直下と SKILL.md の `### WebTransport over HTTP/2 (webtransport.h2)` 直下に「TLS 1.3 のみサポート (draft-15 Section 7 準拠のため。TLS 1.2 + EMS の合法接続も現時点では拒否する)」の注意書きを新設する
- 例外: 将来 TLS 1.2 + EMS の検証手段が Python で公開された場合の緩和は本 issue の対象外とし、必要になれば別途起票する
- `http2.Client` / `http2.Server` (WebTransport ではない HTTP/2) は本 issue の範囲外 (draft-15 の対象は WebTransport のみ)
- テストは TLS 1.3 成功を実 `h2.Server` + 実 `h2.Client` の e2e で、TLS 1.2 拒否を生 `asyncio` + `maximum_version = TLSv1_2` の SSLContext + 自己署名証明書で構成した対向で検証する。本変更で `h2.Server` / `h2.Client` は TLS 1.3 固定になり TLS 1.2 のみの端点を作れないため、拒否される側は公開クラスを使わない
- 変更対象: `src/webtransport/h2/client.py` / `src/webtransport/h2/server.py` / `README.md` / `skills/webtransport-py/SKILL.md` / `tests/test_webtransport_h2_tls_version.py` (新規) / `CHANGES.md` の develop への `[CHANGE]` エントリ (TLS 1.2 接続を拒否する後方互換性のない変更)

## 完了条件

- TLS 1.2 のみのサーバーへの `h2.Client.connect` が接続失敗例外で拒否されること。対向は生 `asyncio.start_server` + `maximum_version = ssl.TLSVersion.TLSv1_2` の SSLContext + 自己署名証明書で構成し、`h2.Client(..., verify_peer=False)` で接続する。実測ではサーバー側の TLS バージョン不一致で接続がリセットされ `ConnectRefusedError` になるため、`ConnectRefusedError` または `HandshakeFailedError` のいずれかを許容する。生対向サーバーはハンドシェイク成立後も接続を保持し HTTP/2 SETTINGS を送らないこと (退行して TLS 1.2 が成立した場合は `ConnectTimeoutError` になりテストが失敗するため、即切断による `ConnectRefusedError` の偽陽性を避ける)
- TLS 1.2 のみのクライアントからの接続がサーバー側 TLS 層で拒否されること。実 `h2.Server` に対し生 `asyncio` + `maximum_version = ssl.TLSVersion.TLSv1_2` のクライアントで接続し、ハンドシェイクの失敗 (接続リセットまたは `ssl.SSLError`) を確認する。自己署名証明書による証明書検証失敗で偽陽性にならないよう、クライアントは `check_hostname=False` / `verify_mode = ssl.CERT_NONE` にする
- TLS 1.2 + EMS の合法接続も拒否される (TLS 1.2 全体を拒否するため包含される。EMS の交渉有無は Python から観測できないため独立した表明はしない。stricter-than-spec である)
- TLS 1.3 での接続は従来どおり成功すること (実 `h2.Server` + 実 `h2.Client` の e2e)
- README / SKILL.md の上記箇所に注意書きが新設されていること
- `tests/test_webtransport_h2_tls_version.py` を新規作成し、上記の TLS 1.2 拒否と TLS 1.3 成功を日本語コメント付きで検証すること
- `CHANGES.md` の develop に `[CHANGE]` エントリが追加されていること
- 既存のテスト全 976 件が引き続き通過すること
