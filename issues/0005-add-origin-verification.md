# WebTransport over HTTP/3 サーバーの Origin ヘッダー検証を実装する

- Created: 2026-08-01
- Completed: YYYY-MM-DD
- Branch: feature/add-origin-verification
- Polished: YYYY-MM-DD

## 目的

draft-ietf-webtrans-http3-16 Section 3.2 の MUST 要件「When the request contains the Origin header, the WebTransport server MUST verify the Origin header」を満たす。ブラウザからの接続は必ず Origin ヘッダーを送るため、現状のサーバーは仕様非準拠で、許可していないオリジンからのアクセスを拒否できない。

## 現状

- `src/bindings/webtransport_h3.cpp` の `end_headers_cb` は `:method` と `:protocol` のみを検査し、Origin ヘッダーを検証しない
- `src/webtransport/h3/server.py` の `_process_webtransport_events` は `SESSION_READY` イベントで無条件に `accept_session()` を呼ぶ
- サーバー側の Origin 検証はコードベースに存在しない。Origin を扱うのは h2 クライアントの送信側 (`src/bindings/webtransport_h2.cpp` の `connect` の `origin` 引数) のみ

## 設計方針

- サーバーに許可オリジンのリストを設定できる窓口を追加する
- Extended CONNECT リクエストのヘッダー処理時に Origin を検証し、許可されていない場合は 403 を返してセッションを拒否する
- Origin ヘッダーが無いリクエストの扱いは仕様上 OPTIONAL のため、実装時に判断する (非ブラウザクライアントからの接続を許可するか)

## 完了条件

- 許可オリジン外からの接続が 403 で拒否される
- 許可オリジンからの接続は従来どおり 2xx で受理される
- モックなしの e2e テストで検証できる
