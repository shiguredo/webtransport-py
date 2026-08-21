# WebTransport over HTTP/2 bindings に SESSION_REJECTED イベントを追加する

- Created: 2026-08-21
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h2-session-rejected-event
- Polished: {YYYY-MM-DD}

## 目的

`src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` の非 2xx 応答分岐は現状 `wt_sessions_.erase(stream_id)` でセッションエントリを削除するのみで、`SESSION_READY` も `SESSION_CLOSED` も push しない (draft-ietf-webtrans-http2-15 §3.2 の「A WebTransport session is established when the server sends a 2xx response」に沿った意味論として、SessionClosed の発火を意図的に避けている)。

しかし高レベル `webtransport.h2.Client.connect()` が非 2xx 拒否で永久ブロックする問題 (0111) を修正するには、拒否イベントを高レベル層に通知する経路が必要である。既存の SessionClosed 意味論を保ちつつ拒否を通知するため、新規イベント `SESSION_REJECTED` を bindings に追加する。

## 現状

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` の非 2xx 応答分岐 (`status_value[0] != '1' && status_value[0] != '2'`) は `h2_session->wt_sessions_.erase(stream_id)` のみを実行し、イベントは push しない
- `H2EventType` (`src/bindings/webtransport_h2.h`) には `SessionReady` / `SessionClosed` はあるが `SessionRejected` は無い
- `H2Event` にも `status_code` フィールドは無い
- `src/webtransport/h2.pyi` の `EventType` / `Event` にも `SESSION_REJECTED` / `status_code` は無い
- 既存テスト `tests/test_webtransport_h2_reject_session.py::test_client_non_2xx_reject_no_session_closed_event` は「非 2xx 拒否で SessionClosed が発火しない」を設計ピンとして守っており、本 issue の追加は SessionClosed を発火させるものではないためこのピンは維持される

## 設計方針

- `src/bindings/webtransport_h2.h` の `H2EventType` 列挙に `SessionRejected` を追加する (SessionReady / SessionClosed と並列に配置)
- `src/bindings/webtransport_h2.h` の `H2Event` 構造体に `uint16_t status_code` フィールドを追加する (SessionRejected 発火時のみ意味を持つ。他のイベント種別では 0)
- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` の非 2xx 応答分岐 (`wt_sessions_.erase(stream_id)` の直前) で `H2EventType::SessionRejected` イベントを push し、`event.session_id` と `event.status_code` に受信した HTTP status code (例: 403) を載せる
- nanobind の `.def_ro("status_code", ...)` を `H2Event` に追加し、Python 側から読めるようにする
- `src/webtransport/h2.pyi` の `EventType` に `SESSION_REJECTED` を追加、`Event` クラスに `status_code: int` プロパティを追加
- 既存 bindings の意味論 (「非 2xx で SessionClosed は発火しない」) は変更しない。SessionRejected は SessionClosed とは別種の新イベントとして追加され、既存の設計ピンテストは影響を受けない

## 完了条件

- `src/bindings/webtransport_h2.h` の `H2EventType` に `SessionRejected` バリアントが追加されている
- `src/bindings/webtransport_h2.h` の `H2Event` に `status_code` フィールドが追加されている
- `src/bindings/webtransport_h2.cpp` の非 2xx 応答分岐で `SessionRejected` イベントが push され、`status_code` に実際の HTTP status code が載る
- `src/webtransport/h2.pyi` に `EventType.SESSION_REJECTED` および `Event.status_code` プロパティが追加され、`ty check` を通る
- `tests/test_webtransport_h2_reject_session.py` に低レベルテストを追加: 非 2xx 応答受信時に `SESSION_REJECTED` イベントが `status_code = 403` (等) 付きで発火することを検証。既存の設計ピンテスト (`test_client_non_2xx_reject_no_session_closed_event`) が引き続き pass することを確認
- `AGENTS.md` のモック禁止に従い、実 `h2_low.Session` 対で Sans-IO テストを追加する
- 全既存テスト pass、`ruff format` / `ruff check` / `ty check` 通過

## 解決方法

- `src/bindings/webtransport_h2.h`:
  - `H2EventType` 列挙に `SessionRejected` を追加
  - `H2Event` 構造体に `uint16_t status_code = 0;` を追加
- `src/bindings/webtransport_h2.cpp`:
  - `on_frame_recv_callback` の非 2xx 応答分岐 (line 2110 付近) で、`wt_sessions_.erase(stream_id)` の直前に以下を追加:
    ```cpp
    H2Event event;
    event.type = H2EventType::SessionRejected;
    event.session_id = stream_id;
    event.status_code = static_cast<uint16_t>(std::stoi(status_value));
    h2_session->push_event(std::move(event));
    ```
  - `NB_MODULE` の `H2EventType` enum export に `.value("SESSION_REJECTED", H2EventType::SessionRejected)` を追加
  - `H2Event` の nanobind export に `.def_ro("status_code", &H2Event::status_code, ...)` を追加
- `src/webtransport/h2.pyi`:
  - `EventType` 列挙に `SESSION_REJECTED` を追加
  - `Event` クラスに `status_code: int` プロパティ (docstring 付き) を追加
- `tests/test_webtransport_h2_reject_session.py`:
  - 新規テスト `test_client_non_2xx_reject_pushes_session_rejected_event`: サーバー役の `h2_low.Session` から 403 を返し、クライアント役の Session に `SESSION_REJECTED` イベント (`event.session_id` が該当セッション、`event.status_code == 403`) が push されることを確認
  - 既存の設計ピンテスト (`test_client_non_2xx_reject_no_session_closed_event` 等) が引き続き pass することの回帰確認
- 変更対象外: `src/webtransport/h2/client.py` (SESSION_REJECTED を高レベルで消費する変更は 0111 のスコープ)
- 変更対象外: `src/webtransport/h2/server.py` (Server 側の拒否 API 追加は 0134 のスコープ)

## 依存関係と関連 issue

- **後続 (0111 の先行必須)**: 0111 (`h2.Client.connect() が非 2xx 拒否で永久ブロックする問題を修正する`) が本 issue のマージを実装着手の前提としている
- **並列 (0134)**: 0134 (`h2.Server に拒否 API を追加する`) と併せて 0111 の依存を構成する。0133 (本 issue) と 0134 の実装順序は独立可能
- **関連**: draft-ietf-webtrans-http2-15 §3.2 準拠として SessionClosed 非発火を保つ点で 0111 の polish で合意された方針に沿う
