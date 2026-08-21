# WebTransport over HTTP/2 高レベル Server に拒否 API を追加する

- Created: 2026-08-21
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h2-server-reject-session-api
- Polished: 2026-08-21

## 目的

高レベル `webtransport.h2.Server` に、CONNECT 要求を非 2xx で拒否する API (`on_session_request` コールバック + 低レベル `h2_low.Session.reject_session(session_id, status_code)` の呼び出し) を追加する。現状は SESSION_READY 受信時に無条件で `session.accept_session(event.session_id)` を呼ぶだけで、拒否判定のフックも高レベル層からの拒否経路も存在しない。低レベル `h2_low.Session.reject_session(session_id, status_code)` は既に実装済み (`src/bindings/webtransport_h2.cpp` の `H2Session::reject_session`) だが、高レベル `Server` からは利用できない。

本 issue の目的は 2 つ:
1. アプリが Origin 検証・URI パス検証・認証等 (受信 HTTP ヘッダー参照が必要な検証) に基づいて WebTransport セッションを非 2xx で拒否できるようにする (draft-ietf-webtrans-http2-15 §3.2 の 403 / 405 の SHOULD を実装可能にする)
2. 0111 (`h2.Client.connect() が非 2xx 拒否で永久ブロックする問題を修正する`) の e2e テストで、実 h2.Server から 403 を返せるようにする

draft-ietf-webtrans-http2-15 §3.2 の逐語引用 (405 / 403 の SHOULD):

> If the target resource does not support WebTransport, the server SHOULD reply with status code 405 (Section 15.5.6 of [HTTP]).

> When the request contains the Origin header, the WebTransport server MUST verify the Origin header to ensure that the specified origin is allowed to access the server in question. If the verification fails, the WebTransport server SHOULD reply with status code 403 (Section 15.5.4 of [HTTP]).

## 現状

- `src/webtransport/h2/server.py` の `Server` クラスには `on_session_request` 相当のコールバック登録 API が無い
- `Server._handle_client` は SESSION_READY 受信時に `session.accept_session(event.session_id)` を無条件で呼び、その直後に `SessionWriter` を生成して `on_session_ready` コールバックを呼ぶ
- `SessionWriter` には `open_stream` / `send_stream_data` / `send_datagram` / `reset_stream` / `close_session` のみで、`reject_session` は無い (SessionWriter が生成されるのは accept 後のため、SessionWriter に `reject_session` を追加する必要は無い。拒否は accept 前に低レベル `h2_low.Session.reject_session` を直接呼ぶ)
- 低レベル `h2_low.Session.reject_session(session_id, status_code)` は既に実装済み (`src/bindings/webtransport_h2.cpp` の `H2Session::reject_session`)。draft §3.2 の非 2xx 応答送出処理はここに実装されている
- 既存テスト `tests/test_webtransport_h2_reject_session.py` は Sans-IO 層のみ。高レベル `webtransport.h2.Server` 経由の拒否テストは存在しない
- 依存関係の前提: 現状の bindings `H2Event` 構造体には `headers` フィールドが存在しない (`type / session_id / stream_id / data / error_code / error_message / fin` の 7 フィールドのみ)。SESSION_READY push 直後に `pending_headers_` は消去されるため、高レベル層に受信ヘッダーを渡す経路が無い。本 issue が求める Origin 検証・URI パス検証を実現するには、依存 1 (0133) 側で SESSION_READY イベントに受信 HTTP ヘッダーを載せる拡張が必要 (下記「依存関係と関連 issue」を参照)

## 設計方針

- `Server.on_session_request(callback)` を追加する。callback のシグネチャは以下:
  ```python
  async def on_session_request(
      session_id: int,
      headers: list[tuple[str, str]],
      addr: tuple[object, ...],
  ) -> int | None:
      """CONNECT 要求を受けたときに呼ばれる。
      戻り値: None → セッションを accept (低レベルは 200 を送出)
              int (200-299) → accept (低レベルは常に 200 を送出。将来の :status 拡張余地)
              int (300-599) → reject。指定した status_code で応答
              上記以外 → 実装側で ValueError を投げる (下記の範囲外扱いを参照)
      """
  ```
- `Server._handle_client` の SESSION_READY 分岐で、`on_session_request` コールバックが登録されていれば呼び出し、戻り値に応じて `session.accept_session(...)` または `session.reject_session(..., status_code)` を呼ぶ。コールバック未登録の場合は現状通り無条件 accept する (後方互換)
- accept 経路では、これまで通り `SessionWriter` を生成し `session_writers` に登録して `on_session_ready` を呼ぶ既存フローを維持する (Step 3 で示すスニペット参照)。reject 経路では `SessionWriter` は生成しない・`session_writers` には登録しない・`on_session_ready` は呼ばない・`on_session_closed` も呼ばない (bindings の `reject_session` が非 2xx で `wt_sessions_` を即消去し SESSION_CLOSED を発火させないため。0133 で新設される `SESSION_REJECTED` イベントもサーバー側 `reject_session` 経路では発火しない設計になっている)
- コールバックの戻り値の解釈:
  - `None`: accept (現状の挙動と同一。低レベルは `:status = 200` を送出)
  - `2xx (200-299)`: accept として扱う。低レベル `H2Session::accept_session` は :status を引数に取らず常に `200` を送出するため、`callback` が `201` を返しても実際には `200` が送出される。将来的に `H2Session::accept_session` が :status を受けられるよう拡張する余地を残すために `int` を受けているが、現状は 200 送出しかできない旨をコールバックの docstring と h2.pyi に明記する
  - `非 2xx (300-599)`: reject。指定した status_code で `session.reject_session` を呼ぶ
  - **上記範囲外 (0-199 / 600 以上 / 負値、`bool` 由来の `False`=0 / `True`=1 を含む)**: 実装側で `ValueError` を投げる。理由: HTTP status code として意味を持たない値を silent に受け入れると、`session.reject_session` に `-1` や `999` が渡って低レベル (`std::to_string(status_code)` で `:status: -1` 等を生成) が壊れる。1xx は accept でも reject でもない中間応答であり、高レベル `on_session_request` API では扱わない (本 issue のスコープ外。1xx を挟んだ最終応答の既知制約 `test_client_receive_1xx_then_final_response_keeps_session` は現状のまま維持)。この判定は callback 呼び出し後、`accept_session` / `reject_session` に流す前に `_handle_client` 側で行う (下記スニペット参照)
- コールバック内で例外が発生した場合の挙動は、既存の `on_session_ready` / `on_stream_data` 等と同じく **捕捉しない**。`_handle_client` の外側 try/finally で writer を close するだけで、接続全体が閉じる (既存挙動と同一)。この挙動を設計方針として明記し、テストでは正常系のみを扱う。ただし上記の範囲外 status_code に対する `ValueError` は callback ではなく `_handle_client` 側で投げるため、同じ経路で接続が閉じる (これも設計上の意図)
- 既存の `on_session_ready` / `on_stream_data` 等と対称的なコールバック登録パターン (`self._on_session_request = callback`) にする
- **既存コールバックとの引数シグネチャの非対称性**: 既存 6 コールバック (`on_session_ready` / `on_session_closed` / `on_stream_data` / `on_stream_reset` / `on_datagram` / `on_error`) はいずれも `SessionWriter` を引数に取るが、`on_session_request` は accept 前に呼ばれるため `SessionWriter` をまだ作れない。この非対称性は仕様上の必然として設計方針に明記する
- コールバック引数の `headers` は SESSION_READY イベントから取得する。この取得には依存 1 (0133) 側で SESSION_READY イベントに受信 HTTP ヘッダーを載せる拡張が必須 (下記「依存関係と関連 issue」参照)
- コールバック引数の `addr` は `writer.get_extra_info('peername')` で取得する。IPv6 socket では 4-tuple `(host, port, flowinfo, scopeid)` を返し、IPv4 では 2-tuple `(host, port)` を返すため、型注釈は `tuple[object, ...]` とする。既存 `h3/server.py` の `_on_session_ready` シグネチャは `tuple[str, int]` (`_normalize_addr` で 2-tuple に正規化) だが、本 issue では h3 と同じ正規化を挟むと Origin 検証時に IPv6 の flowinfo / scopeid が失われる懸念があり、生 tuple のまま渡す。h3 側の正規化方針を統一するかは本 issue のスコープ外 (別 issue で扱う)
- **`writer.get_extra_info('peername')` は対向切断直後などで `None` を返しうる**。`None` の場合は callback を呼び出さず accept 経路に流す。この扱いを選ぶ理由: `peername` が `None` になるのは対向切断直後の限定的なタイミングで、SessionWriter を生成しても実データが流れる前に既存 read ループの EOF 検知経路 (`_handle_client` の `if not received: break`) で接続が閉じるため実害は無い。逆に silent に拒否すると、アプリの security ポリシー (Origin 検証等) を bypass する挙動と読まれかねないため、明示的な reject ではなく accept 側に倒す。この挙動は pyi の docstring にも「`peername` が取得できない場合、callback は呼ばれず accept 経路に流れる」と明記する
- **docstring / コメントには issue 番号 (「0133」等) を書かない** (`shiguredo-issues` 規約)。docstring 側では「SESSION_READY イベントに載る受信 HTTP ヘッダー」等、機能の説明として書く。issue 番号は本 issue 本文と PR 本文でのみ言及する
- Sans-IO 層の意味論 (「非 2xx 拒否時に SessionClosed 非発火」) は変更しない。サーバー側 `reject_session` 経路では 0133 で新設される `SESSION_REJECTED` イベントも発火しない (0133 の設計方針と一致)

## 完了条件

- `webtransport.h2.Server` に `on_session_request(callback)` メソッドが追加され、コールバックの戻り値に応じて accept / reject が切り替わる
- コールバック未登録時は既存通り無条件 accept され、既存 e2e テストが引き続き pass する
- コールバックが `None` または `int (200-299)` を返した場合、accept 経路 (SessionWriter 生成 + `on_session_ready` 呼び出し) が動作する回帰確認テストが pass する
- コールバックが `int (300-599)` を返した場合、対向 Sans-IO `h2_low.Session` (クライアント役) が非 2xx 応答を受信することを検証する (依存 1 (0133) の `SESSION_REJECTED` イベントを対向側で検知して `status_code` を確認する)
- コールバックが範囲外値 (0-199 / 600 以上 / 負値 / `bool`) を返した場合、`ValueError` が投げられ接続が閉じることを検証する
- `AGENTS.md` のモック禁止に従い、実 `webtransport.h2.Server` と対向側 Sans-IO `h2_low.Session` を組み合わせた e2e テストで検証する
- 全既存テスト pass、`ruff format` / `ruff check` / `ty check` 通過
- 実装着手前提: 依存 1 (0133) の再 polish 完了とマージが済んでいること (下記「依存関係と関連 issue」参照)

## 解決方法

- `src/webtransport/h2/server.py`:
  - `if TYPE_CHECKING:` ブロック内の import は既存の `Awaitable` / `Callable` をそのまま利用する。`tuple[object, ...]` は組み込み型なので追加 import は不要 (`shiguredo-python` の「Any を使わない、object で代替できる場合は object」規約に従い、既存 `_normalize_addr` (`h3/server.py`) と同じ型を採用)
  - `Server.__init__` に `self._on_session_request: Callable[[int, list[tuple[str, str]], tuple[object, ...]], Awaitable[int | None]] | None = None` を追加
  - `Server.on_session_request(callback)` メソッドを追加 (既存の `on_session_ready` / `on_stream_data` 等と同じパターン)
  - `Server._handle_client` の SESSION_READY 分岐を以下に変更 (accept 経路の SessionWriter 生成 + on_session_ready 呼び出しを保つこと):
    ```python
    if event.type == h2_low.EventType.SESSION_READY:
        should_accept = True
        if self._on_session_request is not None:
            # peername は対向切断直後などで None を返しうる。
            # None の場合は callback をスキップして accept 経路に流す
            peername = writer.get_extra_info("peername")
            if peername is not None:
                status = await self._on_session_request(
                    event.session_id,
                    event.headers,
                    peername,
                )
                if status is not None:
                    # bool は int のサブクラスなので明示的に弾く
                    if isinstance(status, bool):
                        raise ValueError(
                            f"on_session_request must return None or int, got bool: {status}"
                        )
                    # HTTP status code の妥当範囲は 200-599 のみ受け入れる
                    if not (200 <= status < 600):
                        raise ValueError(
                            f"on_session_request status_code out of range (200-599): {status}"
                        )
                    if status >= 300:
                        # reject
                        session.reject_session(event.session_id, status)
                        should_accept = False
        if should_accept:
            session.accept_session(event.session_id)
            session_writer = SessionWriter(writer, session, event.session_id)
            session_writers[event.session_id] = session_writer
            if self._on_session_ready is not None:
                await self._on_session_ready(session_writer)
    ```
- `src/webtransport/h2.pyi`:
  - `Server` に `on_session_request(callback)` メソッドを追加。docstring に「戻り値 None または int (200-299): accept (低レベルは常に 200 を送出)、int (300-599): reject。範囲外の int や bool は ValueError」旨を記載
  - コールバック引数 `headers` は SESSION_READY イベントに載る受信 HTTP ヘッダー、`addr` は `writer.get_extra_info('peername')` の戻り値 (IPv6 では 4-tuple、IPv4 では 2-tuple、対向切断直後は取得できないケースあり) 旨も記載
  - docstring に「`peername` が取得できない場合、callback は呼ばれず accept 経路に流れる」旨を明記 (アプリ側は callback 内で必ずしも呼ばれない前提で書けるように)
  - docstring には issue 番号を書かないこと
- `tests/test_e2e_webtransport_h2.py` に e2e テスト追加:
  - `test_h2_server_rejects_session_with_non_2xx`: `on_session_request` から 403 を返し、対向 Sans-IO `h2_low.Session` (クライアント役) が 0133 の `SESSION_REJECTED` イベントを受信して `status_code == 403` を確認
  - `test_h2_server_accept_via_on_session_request`: `on_session_request` から `None` または `200` を返し、accept 経路 (`on_session_ready` 発火) が正常動作することを確認
  - `test_h2_server_on_session_request_invalid_status_raises_value_error`: `on_session_request` から範囲外値 (0 / -1 / 100 / 600 / `False`) を返し、`ValueError` が投げられ接続が閉じることを確認
  - 既存の 2xx 経路の e2e テスト (`test_server_client_communication` 相当) が引き続き pass することの回帰確認
- 変更対象: `src/webtransport/h2/server.py` / `src/webtransport/h2.pyi` / `tests/test_e2e_webtransport_h2.py`
- 変更対象外: `src/bindings/webtransport_h2.cpp` の `H2Session::reject_session` (既に実装済み)
- 変更対象外: `src/webtransport/h2/client.py` (0111 のスコープ)
- 変更対象外: `SESSION_REJECTED` イベントの新設 (0133 のスコープ) と `H2Event.headers` フィールドの追加 (0133 の再 polish で繰り込むスコープ)

## 依存関係と関連 issue

- **依存 1 (先行必須)**: 0133 (`WebTransport over HTTP/2 bindings に SESSION_REJECTED イベントを追加する`) — 以下 2 点を先行必須とする:
  1. `H2Event` 構造体に `headers: list[tuple[str, str]]` フィールドを追加し、SESSION_READY イベント push 時に受信 HTTP ヘッダーを載せる。本 issue の `on_session_request` コールバックが Origin 検証・URI パス検証を行うために必須。`src/webtransport/h2.pyi` の `Event` にも `headers` プロパティを追加
  2. `SESSION_REJECTED` イベントの新設 (0133 本来のスコープ)。本 issue の e2e テストで対向 Sans-IO が非 2xx 応答受信を検知するために利用
  - **重要**: 上記 1 は現時点 (2026-08-21) の 0133 の Polished 済みスコープには含まれていない。0133 側で `H2Event.headers` 追加を含む再 polish (Polished 日付更新) が必要で、その後に 0133 → 0134 の順でマージする必要がある。本 issue の実装着手は 0133 の再 polish + マージが完了してから
- **後続 (0111 の先行必須)**: 0111 (`h2.Client.connect() が非 2xx 拒否で永久ブロックする問題を修正する`) が本 issue のマージを実装着手の前提としている
- **並列不可**: 0133 と本 issue の実装順序は 0133 (再 polish 版) → 本 issue の順に固定 (依存 1 の 2 点が本 issue に前提として必要なため)
- **関連**: draft-ietf-webtrans-http2-15 §3.2 の非 2xx 応答 (403 / 405 SHOULD) の高レベル API 化
- **関連 (別 issue 候補)**: 既存 `webtransport.h3.Server` の `_on_session_ready` シグネチャ `tuple[str, int]` (`_normalize_addr` で IPv6 の flowinfo / scopeid を捨てる) と h2 側の `on_session_request` シグネチャ `tuple[object, ...]` (生 tuple) は非対称になる。統一するかは別 issue で扱う
