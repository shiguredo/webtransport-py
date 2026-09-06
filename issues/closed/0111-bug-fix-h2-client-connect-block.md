# h2.Client.connect() が非 2xx 拒否で永久ブロックする問題を修正する

- Created: 2026-08-18
- Completed: 2026-08-23
- Branch: feature/fix-h2-client-connect-block
- Polished: 2026-08-21

## 目的

高レベル `webtransport.h2.Client.connect()` が、サーバーからの非 2xx 応答 (403 等) で拒否された際に永久ブロックする問題を修正する。draft-ietf-webtrans-http2-15 §3.2 は「A WebTransport session is established when the server sends a 2xx response」と定めており、非 2xx 応答はセッション未確立を意味する。現状の高レベル `Client.connect` は `SESSION_READY` / `SESSION_CLOSED` のどちらかを待つ `while self._running:` ループで実装されており、拒否時にはどちらも発火しないためループから抜けられない。

本 issue では、bindings 側に新設される `SESSION_REJECTED` イベント (別 add issue で扱う。下記の依存関係を参照) を高レベル `Client.connect` が消費して `False` を返す実装を追加する。

## 現状

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` の非 2xx 応答分岐 (`status_value[0] != '1' && status_value[0] != '2'` の条件) は、`h2_session->wt_sessions_.erase(stream_id)` でセッションエントリを削除するのみで、`SESSION_READY` も `SESSION_CLOSED` も push しない。既存コメントで「一度も確立されていないセッションの終了通知という意味論が合わない」と明言されており、draft-15 §3.2 準拠として設計ピン化されている (既存テスト `tests/test_webtransport_h2_reject_session.py::test_client_non_2xx_reject_no_session_closed_event` が同意味論を守る)
- `src/webtransport/h2/client.py` の `Client.connect` は Extended CONNECT 送出後に `while self._running:` ループで `SESSION_READY` / `SESSION_CLOSED` のみを待つ。拒否時はどちらも発火しないため、`_receive` が TCP EOF (`_reader.read()` が空バイト) を検出するか、外部から `close()` が呼ばれるまでループを抜けられない
- 補足: 非 2xx 応答で bindings が `wt_sessions_.erase` すると、その後 `on_stream_close_callback` が呼ばれた際も `get_wt_session(stream_id)` が失敗して `SESSION_CLOSED` イベントを push しない。結果として、HTTP/2 ストリーム自体はサーバー側のみが閉じた半開きのまま接続終了まで残るケースがあり、TCP EOF 待ちの経路もタイミング次第で発火しない
- 既存テスト `tests/test_webtransport_h2_reject_session.py` の非 2xx 拒否テストは Sans-IO 層 (`h2_low.Session` 直接) のみで、高レベル `Client.connect` の挙動は未カバー
- 高レベル `src/webtransport/h2/server.py` の `_handle_client` は `SESSION_READY` 受信時に `session.accept_session(event.session_id)` を無条件で呼ぶだけで、拒否判定のフック (`on_session_request` 等) も `SessionWriter` 相当への `reject_session` API も存在しない。実 h2.Server で 403 を返す e2e テストを書くには、この Server 側 API 追加が別途必要
- draft-ietf-webtrans-http2-15 §3.2 の逐語引用:
  > A WebTransport session is established when the server sends a 2xx response.

## 設計方針

- 別 add issue で追加される `webtransport.h2.EventType.SESSION_REJECTED` イベントを、`Client.connect` の while ループで検知して `False` を返す
- `SESSION_REJECTED` イベントには少なくとも「セッション ID」と「HTTP status code」が載る想定 (別 add issue で確定)。本 issue のスコープでは status code を利用しない (`False` を返すのみ)。status code をアプリへ通知する API 追加は、拒否イベントの新設を担う別 add issue または追加の別 add issue で扱う
- 既存 bindings の意味論 (「非 2xx で SessionClosed は発火しない、黙って削除する」) は draft-15 §3.2 準拠として保つ。`SESSION_REJECTED` は SessionClosed と別種の新イベントとして追加され、既存の設計ピンテスト (`test_client_non_2xx_reject_no_session_closed_event`) は影響を受けない
- `connect()` のシグネチャは変更しない (既存の `async def connect(self) -> bool` を維持、拒否時は `False` を返す)。QUIC 層と対称なタイムアウト引数の追加は本 issue の対象外 (別 add issue で扱う。下記の依存関係を参照)
- 1xx 中間応答を挟んだ最終応答は既知の制約として本 issue の対象外 (`src/bindings/webtransport_h3.cpp` のコメントおよび既存テスト `test_client_receive_1xx_then_final_response_keeps_session` で明示済み)

## 依存関係と関連 issue

- **依存 1 (先行必須)**: 0133 (`WebTransport over HTTP/2 bindings に SESSION_REJECTED イベントを追加する`) — bindings (`src/bindings/webtransport_h2.cpp`) に `SESSION_REJECTED` イベントを新設する add issue。`H2EventType` 列挙に `SessionRejected` を追加し、`H2Event` に `status_code` フィールドを追加、`on_frame_recv_callback` の非 2xx 分岐でイベントを push する変更。`src/webtransport/h2.pyi` の `EventType` / `Event` も同時に更新。**この issue のマージが本 issue の実装着手前提**
- **依存 2 (先行必須)**: 0134 (`WebTransport over HTTP/2 高レベル Server に拒否 API を追加する`) — 高レベル `src/webtransport/h2/server.py` に拒否 API を追加する add issue。`Server.on_session_request(callback)` を追加し、コールバックが非 2xx status code を返した場合に `session.reject_session(session_id, status_code)` を呼ぶ実装。**e2e テストで実 h2.Server から 403 を返せるようにするため、本 issue の実装着手前提**
- **関連 (同時期の open、対象領域重複)**: 0110 (`h2.Client の on_session_ready コールバックが発火しない問題`) は本 issue と同じ `Client.connect` の while ループを触る。本 issue の実装は 0110 の設計方針 (「connect() でイベントを消費せずにコールバック発火経路を確保する」) と衝突しないよう、実装順序 (0110 → 0111 or 0111 → 0110) と共通コード変更の重複を実装時に確認する
- **関連 (対象領域重複)**: 0104 (`WebTransport over HTTP/2 のクライアントが 2xx 非 200 応答をセッション確立として扱わない問題`) は bindings の 2xx 判定を「200 のみ」から「先頭が '2'」に拡張する修正。0104 未修正状態では 201 等の 2xx 非 200 応答も本 issue と同じくハングするが、原因層 (bindings 判定) が異なるため 0111 の対象外 (「201 は 0104 で扱う」)
- **対象外の別 add issue 候補 (未起票)**:
  - `connect()` タイムアウト引数追加 (QUIC 層 `Client.connect(timeout: float = 10.0)` と対称にする)
  - 拒否時の HTTP status code をアプリへ通知する API (`on_session_rejected` コールバック等、依存 1 と同時 or 追加 issue で扱う)

## 完了条件

- サーバーが非 2xx 応答 (例: 403) で WebTransport CONNECT を拒否したとき、`webtransport.h2.Client.connect()` が `SESSION_REJECTED` イベント受信直後 (次の while 反復) に `False` を返して終了する (現状は永久ブロック。判定は「SESSION_REJECTED を発火させたのに `False` が返らない」場合をバグとして検知できる粒度)
- `Client.connect` の while ループが `SESSION_REJECTED` イベントを既存の `SESSION_READY` / `SESSION_CLOSED` と同じ形で処理する分岐を持つ
- 既存の `SESSION_READY` (2xx 応答による確立) と `SESSION_CLOSED` (確立後の終了) の経路が本修正で壊れていないことを、既存 e2e テストの引き続き pass で担保する
- `AGENTS.md`「モックやスタブは絶対に利用しないこと」に従い、実 `webtransport.h2.Server` (依存 2 で追加される拒否 API を利用) と `webtransport.h2.Client` を組み合わせた e2e テストで、非 2xx 拒否 (Server 側が 403 等を返す) → Client 側 `connect()` が `False` を返すことを検証する
- 追加テストにはコメントで意図・前提・期待値を日本語で明記する
- 全既存テスト pass、`ruff format` / `ruff check` / `ty check` 通過

## 解決方法

- `src/webtransport/h2/client.py` の `Client.connect` メインループに以下の分岐を追加:
  ```python
  if event.type == h2_low.EventType.SESSION_REJECTED and event.session_id == self._session_id:
      self._connected = False
      return False
  ```
  既存の `SESSION_READY` / `SESSION_CLOSED` 分岐と同じ位置・同じ形にする
- `tests/test_e2e_webtransport_h2.py` に e2e テストを追加:
  - `test_h2_client_connect_returns_false_on_non_2xx_reject`: 依存 2 で追加される Server 側の `on_session_request` コールバックで `False` (拒否) を返すよう設定し、Server が 403 を送出、Client 側 `connect()` が `False` を返すことを検証
  - 既存の 2xx 経路の e2e テスト (`test_server_client_communication` 相当) が引き続き pass することの回帰確認
- 変更対象: `src/webtransport/h2/client.py` / `tests/test_e2e_webtransport_h2.py`
- 変更対象外: `src/bindings/webtransport_h2.cpp` (`SESSION_REJECTED` イベント新設は依存 1 の別 add issue で完了済みを前提とする)
- 変更対象外: `src/webtransport/h2/server.py` (拒否 API 追加は依存 2 の別 add issue のスコープ)
- 変更対象外: `src/webtransport/h2.pyi` (`EventType.SESSION_REJECTED` の追加は依存 1 の別 add issue のスコープ)
- 変更対象外: `Client.connect` のシグネチャ (タイムアウト追加は別 add issue のスコープ)
