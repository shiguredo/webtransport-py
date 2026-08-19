# h3 / http3 サーバーの高レベル API を拡充する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-http3-server-capabilities
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/3 (`h3.Server`) と HTTP/3 (`http3.Server`) の高レベルサーバーに欠落している機能を追加する。セッション拒否の手段と Connection Migration への対応である。

## 現状

- **サーバー側に reject_session の高レベル API がない**: `src/webtransport/h3/server.py` の `Server` は SESSION_READY で自動 accept_session() するため、アプリはセッションを拒否できない。draft-ietf-webtrans-http3-16 Section 3.2 は 403 / 405 拒否を規定しており、拒否手段がないと仕様の拒否シナリオをアプリで実現できない。なお `h2.Server` にも同様の問題があり、`h2.Config` には allowed_origins 自体が存在しない (draft-15 Section 3.2 の Origin 検証 MUST を満たす手段がない。H3 側は verify_origin 実装済み)
- **h3 / http3 サーバーが Connection Migration 未対応**: `src/webtransport/quic/server.py` は unknown アドレスからの short header パケットを既存接続へ試すが、`src/webtransport/h3/server.py` と `src/webtransport/http3/server.py` は unknown アドレスのパケットを常に新規 accept しようとし、失敗 (RuntimeError) で破棄する。クライアントが接続移行すると接続が失われる

## 設計方針

- `h3.Server` にセッション拒否の高レベル API (reject_session の委譲) を追加する。あわせて `h2.Server` の拒否 API と Origin 検証の手段も検討する
- `h3.Server` / `http3.Server` の run() で、quic.Server と同様に unknown アドレスからのパケットを既存接続の移行候補として処理する

## 完了条件

- サーバー側でセッションを拒否できる高レベル API が追加され、テストがある
- クライアントの接続移行 (Connection Migration) 後に h3 / http3 サーバーが接続を維持できる
