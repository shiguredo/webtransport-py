# WebTransport over HTTP/2 のサーバーに Origin 検証 (allowed_origins) を実装する

- Created: 2026-09-07
- Completed: 2026-09-12
- Branch: feature/add-h2-origin-verification
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 3.2 は「When the request contains the Origin header, the WebTransport server MUST verify the Origin header to ensure that the specified origin is allowed to access the server in question」を求める。`H2SessionConfig` に `allowed_origins` が無く、`H2Session::on_frame_recv_callback` の CONNECT 判定は Origin ヘッダーを見ない。高レベル `h2.Server.on_session_request` にヘッダーが渡るのでアプリ側で拒否は可能だが、既定で検証しない = 仕様違反。加えて `peername` が None の場合はコールバック自体をスキップして accept する経路もある。h3 側は `allowed_origins` で対称の検証を実装済み。

## 現状

- `src/bindings/webtransport_h2.h` の `H2SessionConfig` に `allowed_origins` フィールドが無い
- `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` の CONNECT 判定は `:method` / `:protocol` / `webtransport-init` しか見ない
- `src/webtransport/h2/server.py` の `Server._handle_client` は `on_session_request` の `peername` が None の場合コールバックをスキップして accept 経路に流す
- 対照: `src/bindings/webtransport_h3.h` の `H3SessionConfig::allowed_origins`、`webtransport_h3.cpp` の `H3Session::verify_origin` (RFC 6454 の byte-exact 比較) は実装済み
- draft-15 Section 3.2「the WebTransport server MUST verify the Origin header」「If the verification fails, the WebTransport server SHOULD reply with status code 403」
- 現状のテスト `tests/test_e2e_webtransport_h2.py` の Origin 拒否ケースは 0 件

## 設計方針

- `H2SessionConfig` に `std::vector<std::string> allowed_origins` を追加 (h3 側の `H3SessionConfig::allowed_origins` と対称)
- `H2Session::verify_origin` を追加し、`H3Session::verify_origin` と同一ロジック (RFC 6454 の byte-exact、空値 / 複数 Origin の拒否) を実装する。共通ヘルパーへの切り出し (`src/bindings/` の共通ファイル) を検討する
- `on_frame_recv_callback` の CONNECT 判定成立時、Origin 検証に失敗した場合は `reject_session(session_id, 403)` を呼ぶ (draft-15 Section 3.2 の SHOULD 403)
- 高レベル `h2.Server` の `__init__` に `allowed_origins: list[str] | None = None` を追加し、`H2SessionConfig` に反映する (h3.Server と対称)
- `on_session_request` は `peername` の有無に関わらず一貫して呼ぶよう修正する (別 issue 相当だが本 issue の副産物として整理)
- 既存の h3 側実装 (`verify_origin` / `allowed_origins`) の重複を将来的に共通化する布石を残す

## 完了条件

- `h2.Server(allowed_origins=[...])` で Origin 検証が有効になり、許可外オリジンからの CONNECT が 403 で拒否されること
- `allowed_origins` 未設定時は従来どおり全 Origin を受理すること
- Origin ヘッダー無しのリクエストは従来どおり受理すること
- `tests/test_e2e_webtransport_h2.py` に Origin 検証の受理・拒否ケースを追加すること
- 既存のテスト全 822 件が引き続き通過すること

## 解決方法

WebTransport over HTTP/2 のサーバーに Origin 検証を実装した。

- `src/bindings/webtransport_h2.h` の `H2SessionConfig` に `allowed_origins` を追加し、`H2Session::verify_origin` を追加した。ロジックは `H3Session::verify_origin` と同一 (許可リストが空なら常に受理、Origin ヘッダー無しは受理、複数 Origin / 空値 / 許可リスト不一致は拒否。RFC 6454 の正規化は行わない byte-exact 比較)
- `src/bindings/webtransport_h2.cpp` の CONNECT 判定成立時に `verify_origin` を呼び、失敗したら `reject_session(stream_id, 403)` で拒否するようにした (draft-15 Section 3.2 の MUST と 403 SHOULD)。`allowed_origins` を nanobind で公開し、型スタブを再生成した
- `src/webtransport/h2/server.py` の `Server.__init__` に `allowed_origins: list[str] | None = None` を追加し、接続ごとの Config に反映するようにした (`h3.Server` と対称)
- **副産物の修正**: `_handle_client` は `peername` が `None` の場合に `on_session_request` をスキップして accept していた。Origin 検証や認可をアプリに委ねた設計で検証を素通りできてしまうため、`None` の場合は空 tuple を渡して必ず呼ぶように変更した (docstring も更新)
- テストを 4 本追加した。`test_h2_server_rejects_disallowed_origin` (403 と SESSION_REJECTED)、`test_h2_server_accepts_allowed_origin`、`test_h2_server_accepts_request_without_origin`、`test_h2_server_without_allowed_origins_accepts_any_origin` (既定の後方互換)
- `skills/webtransport-py/SKILL.md` の `h2.Server` / `h2.Config` の記述を更新した

`verify_origin` の共通ヘルパー化 (h3 との重複解消) は 0125 (重複コード共通化) の範囲として見送った。`uv run pytest tests/ --timeout=30` の 1097 件が全て通る。
