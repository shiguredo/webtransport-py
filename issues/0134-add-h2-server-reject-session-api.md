# WebTransport over HTTP/2 高レベル Server に拒否 API を追加する

- Created: 2026-08-21
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h2-server-reject-session-api
- Polished: {YYYY-MM-DD}

## 目的

高レベル `webtransport.h2.Server` に、CONNECT 要求を非 2xx で拒否する API (`on_session_request` コールバック + `reject_session(session_id, status_code)`) を追加する。現状は SESSION_READY 受信時に無条件で `session.accept_session(event.session_id)` を呼ぶだけで、拒否判定のフックも拒否メソッドも存在しない。低レベル `h2_low.Session.reject_session(session_id, status_code)` は既に実装済み (`src/bindings/webtransport_h2.cpp` の `H2Session::reject_session`) だが、高レベル `Server` からは利用できない。

本 issue の目的は 2 つ:
1. アプリが Origin 検証・URI パス検証・認証等に基づいて WebTransport セッションを非 2xx で拒否できるようにする (draft-ietf-webtrans-http2-15 §3.4 の 403 / 405 の SHOULD を実装可能にする)
2. 0111 (`h2.Client.connect() が非 2xx 拒否で永久ブロックする問題を修正する`) の e2e テストで、実 h2.Server から 403 を返せるようにする

## 現状

- `src/webtransport/h2/server.py` の `Server` クラスには `on_session_request` 相当のコールバック登録 API が無い
- `Server._handle_client` (line 280-389 相当) は SESSION_READY 受信時に `session.accept_session(event.session_id)` を無条件で呼ぶ
- `SessionWriter` (line 37-129 相当) には `open_stream` / `send_stream_data` / `send_datagram` / `reset_stream` / `close_session` のみで、`reject_session` は無い
- 低レベル `h2_low.Session.reject_session(session_id, status_code)` は既に実装済み (`src/bindings/webtransport_h2.cpp` line 1490 付近)。draft §3.4 の非 2xx 応答送出処理はここに実装されている
- 既存テスト `tests/test_webtransport_h2_reject_session.py` は Sans-IO 層のみ。高レベル `webtransport.h2.Server` 経由の拒否テストは存在しない

## 設計方針

- `Server.on_session_request(callback)` を追加する。callback のシグネチャは以下:
  ```python
  async def on_session_request(
      session_id: int,
      headers: list[tuple[str, str]],
      addr: tuple[str, int],
  ) -> int | None:
      """CONNECT 要求を受けたときに呼ばれる。
      戻り値: None または 2xx status code → セッションを accept
              非 2xx status code (403 等) → セッションを reject
      """
  ```
- `Server._handle_client` の SESSION_READY 分岐で、`on_session_request` コールバックが登録されていれば呼び出し、戻り値に応じて `session.accept_session(...)` または `session.reject_session(..., status_code)` を呼ぶ。コールバック未登録の場合は現状通り無条件 accept する (後方互換)
- コールバックの戻り値は `int | None`:
  - `None`: accept (現状の挙動)
  - `2xx (200-299)`: accept (200 として扱う。将来的な 201 等の対応余地を残す)
  - `非 2xx (300-599)`: reject。指定した status_code で `session.reject_session` を呼ぶ
- 既存の `on_stream_reset` 等と対称的なコールバック登録パターン (`self._on_session_request = callback`) にする
- Sans-IO 層の意味論 (「非 2xx 拒否時に SessionClosed 非発火」) は変更しない (0133 で新設される `SESSION_REJECTED` イベントもここで push される必要はない。0133 のスコープ)

## 完了条件

- `webtransport.h2.Server` に `on_session_request(callback)` メソッドが追加され、コールバックの戻り値に応じて accept / reject が切り替わる
- コールバック未登録時は既存通り無条件 accept され、既存 e2e テストが引き続き pass する
- e2e テストで、`on_session_request` から 403 を返したセッションに対し、`h2_low.Session.reject_session(..., 403)` が呼ばれ、対向クライアント (Sans-IO or 実 h2.Client) が非 2xx 応答を受信することを検証する
- `AGENTS.md` のモック禁止に従い、実 `webtransport.h2.Server` と対向側 (Sans-IO `h2_low.Session` または実 `h2.Client`) を組み合わせた e2e テストで検証
- 全既存テスト pass、`ruff format` / `ruff check` / `ty check` 通過

## 解決方法

- `src/webtransport/h2/server.py`:
  - `Server.__init__` に `self._on_session_request: Callable[[int, list[tuple[str, str]], tuple[str, int]], Awaitable[int | None]] | None = None` を追加
  - `Server.on_session_request(callback)` メソッドを追加 (既存の `on_request` / `on_data` 等と同じパターン)
  - `Server._handle_client` の SESSION_READY 分岐を以下に変更:
    ```python
    if self._on_session_request is not None:
        status = await self._on_session_request(
            event.session_id,
            event.headers,
            addr,
        )
        if status is None or 200 <= status < 300:
            session.accept_session(event.session_id)
        else:
            session.reject_session(event.session_id, status)
    else:
        session.accept_session(event.session_id)
    ```
- `tests/test_e2e_webtransport_h2.py` に e2e テスト追加:
  - `test_h2_server_rejects_session_with_non_2xx`: `on_session_request` から 403 を返し、対向 Sans-IO `h2_low.Session` (クライアント役) が非 2xx 応答を受信することを確認
  - 既存の 2xx 経路の e2e テスト (`test_server_client_communication` 相当) が引き続き pass することの回帰確認
- 変更対象: `src/webtransport/h2/server.py` / `tests/test_e2e_webtransport_h2.py`
- 変更対象外: `src/bindings/webtransport_h2.cpp` (`H2Session::reject_session` は既に実装済み)
- 変更対象外: `src/webtransport/h2/client.py` (0111 のスコープ)
- 変更対象外: `SESSION_REJECTED` イベントの新設 (0133 のスコープ)

## 依存関係と関連 issue

- **後続 (0111 の先行必須)**: 0111 (`h2.Client.connect() が非 2xx 拒否で永久ブロックする問題を修正する`) が本 issue のマージを実装着手の前提としている
- **並列 (0133)**: 0133 (`bindings に SESSION_REJECTED イベントを追加する`) と併せて 0111 の依存を構成する。0133 と 0134 (本 issue) の実装順序は独立可能
- **関連**: draft-ietf-webtrans-http2-15 §3.4 の非 2xx 応答 (403 / 405 SHOULD) の高レベル API 化
