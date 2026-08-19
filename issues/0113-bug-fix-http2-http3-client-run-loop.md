# http2 / http3 クライアントの run() がプロトコルエラー後に永久ループする問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http2-http3-client-run-loop
- Polished: {YYYY-MM-DD}

## 目的

高レベル `http2.Client.run()` と `http3.Client.run()` が、プロトコルエラーで接続が死んだ後に永久ループする問題を修正する。エラーを検知して終了する手段が無い。

## 現状

- `src/webtransport/http2/client.py` の `Client.run` は GO_AWAY 以外にループ終了条件がなく、`is_closed()` もチェックしない。プロトコルエラーで receive() が 0 を返し続けても無限ループする
- `src/webtransport/http3/client.py` の `Client.run` も同様 (HTTP/3 層のエラーは黙って握りつぶされる)
- サーバー側 (`src/webtransport/http2/server.py`) には is_closed() チェックがあるため、クライアント側だけが欠落している

## 設計方針

- 両クライアントの run() に is_closed() チェックとエラー検知の終了条件を追加する
- エラー理由をアプリへ通知する手段も併せて検討する

## 完了条件

- プロトコルエラー時に run() がハングせず終了する
- テストが追加される
