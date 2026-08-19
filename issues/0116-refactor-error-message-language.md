# エラーメッセージとコメントの言語規約違反を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-error-message-language
- Polished: {YYYY-MM-DD}

## 目的

AGENTS.md の規約「ログメッセージは全て英語」「コメントは全て日本語」に違反している箇所を修正する。Python 側の RuntimeError メッセージが日本語のまま残っており、C++ 側に英語コメントが残っている。

## 現状

- RuntimeError のメッセージが日本語のままの箇所 (11 箇所):
  - `src/webtransport/quic/server.py` の `Server.run` 等 (「サーバーが開始されていません」)
  - `src/webtransport/http2/server.py` (「サーバーが開始されていません」)
  - `src/webtransport/http3/server.py` (「サーバーが開始されていません」)
  - `src/webtransport/h2/server.py` (「サーバーが開始されていません」)
  - `src/webtransport/h3/server.py` (「サーバーが開始されていません」「クライアントが接続されていません」)
  - `src/webtransport/h2/client.py` (「クライアントが接続されていません」)
  - `src/webtransport/h3/client.py` (「クライアントが接続されていません」)
  - `src/webtransport/http3/client.py` (「クライアントが接続されていません」)
  - `src/webtransport/http2/client.py` (「クライアントが接続されていません」)
- 英語コメント (規約違反): `src/bindings/http3.cpp` の受信ストリーム構成コメント 4 行
- テストの assert メッセージは日本語で正しい (こちらは規約通り)

## 設計方針

- RuntimeError メッセージを英語に統一する (例: "server is not started" / "client is not connected")
- `src/bindings/http3.cpp` の英語コメントを日本語に直す
- メッセージ文言の変更はテストが文言に依存していないことを確認する

## 完了条件

- RuntimeError メッセージが全て英語になる
- C++ のコメントが全て日本語になる
- 全テストが通る
