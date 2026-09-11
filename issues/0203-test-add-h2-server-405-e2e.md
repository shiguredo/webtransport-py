# 高レベル h2 Server の 405 拒否 (Allow: CONNECT) を e2e で検証する

- Created: 2026-09-11
- Completed: {YYYY-MM-DD}
- Branch: feature/test-h2-server-405-e2e
- Polished: {YYYY-MM-DD}

## 目的

closed/0173 で非 WT リクエストへの 405 と、`reject_session(405)` への `Allow: CONNECT` 付与を実装したが、高レベル `h2.Server.on_session_request` が 405 を返す経路の e2e テストが無い。既存 e2e (`tests/test_e2e_webtransport_h2.py`) は 403 の拒否のみを検証しており、draft-ietf-webtrans-http2-15 Section 3.2 の 405 SHOULD の実運用経路 (アプリのコールバック拒否) の配線が未検証である。低レベルテスト (`tests/test_webtransport_h2_reject_session.py`) は `H2Session::reject_session` 単体の表明であり、高レベル層から `allow` がワイヤに載ることは保証していない。TLS / asyncio を挟んだ実経路で固定する。

## 現状

- `tests/test_e2e_webtransport_h2.py` の `test_h2_server_rejects_session_with_non_2xx` は `on_session_request` が 403 を返すケースのみで、クライアントの `SESSION_REJECTED` の `status_code` を表明する
- 同ファイルの Sans-IO クライアントは `h2_low.Session` を使う。`h2_low.Session` の `SESSION_REJECTED` イベントは `headers` が空のため `allow` を観測できない (closed/0173 の完了条件と同じ制約)
- `src/webtransport/h2/server.py` の `on_session_request` の 405 経路は `session.reject_session(event.session_id, status)` を呼ぶだけで、`Allow` の生成は `src/bindings/webtransport_h2.cpp` の `H2Session::reject_session` に一元化されている
- closed/0173 の低レベルテストでは、応答ヘッダーを観測するクライアントに `webtransport.http2.Connection` (Sans-IO) を使っている

## 設計方針

- `tests/test_e2e_webtransport_h2.py` に、`on_session_request` が 405 を返す e2e テストを追加する
- 応答ヘッダーを観測するクライアントには `webtransport.http2.Connection` (Sans-IO) を使い、TLS ソケット越しに WT CONNECT (`submit_request` + `send_data(..., eof=True)`) を送り、HEADERS イベントで `:status` 405 と `allow: CONNECT` を表明する
- 既存の `_h2_server_with_sans_io_client` は `h2_low.Session` を生成するため、`http2.Connection` を生成する補助ヘルパーの追加または既存ヘルパーの拡張で対応する
- 変更対象: `tests/test_e2e_webtransport_h2.py` / `tests/conftest.py` (ヘルパー追加が必要な場合) / `CHANGES.md` の `### misc` へのエントリ

## 完了条件

- 高レベル `h2.Server.on_session_request` が 405 を返したとき、Sans-IO クライアントの HEADERS イベントで `:status` 405 と `allow: CONNECT` が観測できる e2e テストが追加されていること
- 既存のテストが引き続き通過すること
