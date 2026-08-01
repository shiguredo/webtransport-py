# WebTransport over HTTP/3 サーバーの Origin ヘッダー検証を実装する

- Created: 2026-08-01
- Completed: 2026-08-01
- Branch: feature/add-origin-verification
- Polished: 2026-08-01

## 目的

draft-ietf-webtrans-http3-16 Section 3.2 の MUST 要件「When the request contains the Origin header, the WebTransport server MUST verify the Origin header」を満たす。ブラウザからの接続は必ず Origin ヘッダーを送るため、現状のサーバーは仕様非準拠で、許可していないオリジンからのアクセスを拒否できない。

## 現状

- `src/bindings/webtransport_h3.cpp` の `end_headers_cb` は `:method` と `:protocol` のみを検査し、Origin ヘッダーを検証しない
- `src/webtransport/h3/server.py` の `_process_webtransport_events` は `SESSION_READY` イベントで無条件に `accept_session()` を呼ぶ
- サーバー側の Origin 検証はコードベースに存在しない。h3 クライアント (`src/bindings/webtransport_h3.cpp` の `connect`) も Origin ヘッダーを送信しない。Origin ヘッダーを送信できるのは h2 クライアントの送信側 (`src/bindings/webtransport_h2.cpp` の `connect` の `origin` 引数) のみ

## 設計方針

- 高レベル `Server` (`src/webtransport/h3/server.py`) のコンストラクタに許可オリジンのリスト (`allowed_origins`) を追加し、`_create_connection` で低レベル `Config` の `allowed_origins` に設定して `Session.create_server` に渡す (C++ 層の `end_headers_cb` で検証に使う)。許可リストが未設定 (空) の場合は従来どおり全オリジンを受理する
- Extended CONNECT リクエストのヘッダー処理時 (`src/bindings/webtransport_h3.cpp` の `end_headers_cb`) に Origin を検証し、許可されていない場合は `reject_session` で 403 を返してセッションを拒否する (SESSION_READY イベントを発行せず、`session_ids_` にも登録しない)。403 応答は仕様上 SHOULD (draft-ietf-webtrans-http3-16 Section 3.2) であり、実装では 403 を返す
- Origin ヘッダーの提供は仕様上 OPTIONAL (非ブラウザクライアント) であり、サーバーが Origin ヘッダーが無いリクエストをどう扱うかは仕様で規定されていない。Origin ヘッダーが無いリクエストは従来どおり受理する (既存の e2e テストは Origin ヘッダーを送信しないため)
- HTTP/2 サーバー側の Origin 検証は本 issue の対象外とする

## 完了条件

- Origin ヘッダーを送信する許可オリジン外からの接続が 403 で拒否される
- 許可オリジンからの接続は従来どおり 2xx で受理される
- モックなしの e2e テストで検証できる (Origin ヘッダーを送信する h3 クライアントは別 issue で追加する)

## 解決方法

- `src/bindings/webtransport_h3.h` の `H3SessionConfig` に `allowed_origins` (許可オリジンリスト。空なら全オリジン受理) を追加した
- `src/bindings/webtransport_h3.cpp` に `H3Session::verify_origin` を追加し、`end_headers_cb` で CONNECT リクエストの Origin ヘッダーを検証する。複数・空値の Origin ヘッダーと許可リスト外のオリジンは 403 で拒否し、SESSION_READY を発行せずセッション ID にも登録しない (draft-ietf-webtrans-http3-16 Section 3.2 の MUST / SHOULD 403)。Origin ヘッダーが無いリクエストは従来どおり受理する (OPTIONAL)
- `src/webtransport/h3/server.py` の `Server` コンストラクタに `allowed_origins` を追加し、`_create_connection` で低レベル `Config` に設定する
- `tests/test_e2e_webtransport_h3.py` に e2e テストを 4 本追加した: 許可オリジンの 2xx 受理 (クライアント側 SESSION_READY で確認)、非許可オリジンの拒否 (サーバー側セッション不確立で確認)、allowed_origins 設定時の Origin なし受理、allowed_origins 未設定時の全オリジン受理
