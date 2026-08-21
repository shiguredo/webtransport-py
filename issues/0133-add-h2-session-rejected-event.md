# WebTransport over HTTP/2 bindings に SESSION_REJECTED イベントを追加する

- Created: 2026-08-21
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h2-session-rejected-event
- Polished: 2026-08-21

## 目的

`src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` の非 2xx 応答分岐は現状 `wt_sessions_.erase(stream_id)` でセッションエントリを削除するのみで、`SESSION_READY` も `SESSION_CLOSED` も push しない (draft-ietf-webtrans-http2-15 §3.2 の「A WebTransport session is established when the server sends a 2xx response」に沿った意味論として、SessionClosed の発火を意図的に避けている)。

しかし高レベル `webtransport.h2.Client.connect()` が非 2xx 拒否で永久ブロックする問題 (0111) を修正するには、拒否イベントを高レベル層に通知する経路が必要である。既存の SessionClosed 意味論を保ちつつ拒否を通知するため、新規イベント `SESSION_REJECTED` を bindings に追加する。

draft-ietf-webtrans-http2-15 §3.2 の逐語引用:

> A WebTransport session is established when the server sends a 2xx response.

拒否理由を高レベル層に通知する意義は、この「セッション未確立 (非 2xx)」の状態を実装が識別してアプリへ返せるようにすることにある (実装例として §3.2 は Origin 検証失敗時の 403 SHOULD を挙げている)。

## 現状

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` の非 2xx 応答分岐 (`status_value[0] != '1' && status_value[0] != '2'` の条件) は、`h2_session->wt_sessions_.erase(stream_id)` のみを実行し、イベントは push しない (該当分岐は「非 2xx 応答分岐条件が判定される位置」から「`wt_sessions_.erase(stream_id)` が呼ばれる位置」までの範囲。既存コメントで「SessionClosed は発火しない (黙って削除)」を draft §3.2 準拠として明言)
- `H2EventType` (`src/bindings/webtransport_h2.h` の `enum class`) には `SessionReady` / `SessionClosed` / `SessionDraining` / `StreamData` / `StreamReset` / `StopSending` / `Datagram` / `Error` の 8 バリアントがあり、`SessionRejected` は無い
- `H2Event` 構造体 (`src/bindings/webtransport_h2.h`) には `type` / `session_id` / `stream_id` / `data` / `error_code` / `error_message` / `fin` の 7 フィールドがあり、`status_code` は無い
- `src/webtransport/h2.pyi` の `EventType` / `Event` にも `SESSION_REJECTED` / `status_code` は無い
- 既存テスト `tests/test_webtransport_h2_reject_session.py::test_client_non_2xx_reject_no_session_closed_event` は「非 2xx 拒否で SessionClosed が発火しない」を設計ピンとして守っており、本 issue の追加は SessionClosed を発火させるものではないためこのピンは維持される
- 対称性の参考: `H3EventType` / `H3Event` (`src/bindings/webtransport_h3.h`) にも同種の `SessionRejected` / `status_code` は存在しない。HTTP/3 版 (0112 で扱う `h3.Client.connect()` の 2xx 未待機バグ) が別方式を採らず HTTP/2 と同型で対応するかは 0112 側で判断する (本 issue は HTTP/2 のみを扱う。詳細は下記の依存関係を参照)

## 設計方針

- `src/bindings/webtransport_h2.h` の `H2EventType` 列挙に `SessionRejected` を追加する (SessionReady / SessionClosed と並列に配置)
- `src/bindings/webtransport_h2.h` の `H2Event` 構造体に `uint16_t status_code = 0;` フィールドを追加する。`SessionRejected` 発火時のみ意味を持ち、他のイベント種別ではデフォルト値 `0` のまま利用される (この意図を `.h` の該当フィールド上にコメントで明記する)
- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` の非 2xx 応答分岐 (`wt_sessions_.erase(stream_id)` の直前) で `H2EventType::SessionRejected` イベントを push し、`event.session_id` と `event.status_code` に受信した HTTP status code (例: 403) を載せる。`event.error_code` はデフォルト `0` のまま (nghttp2 の HTTP/2 error code を載せる既存 SessionClosed 経路とは意味論が異なるため、status_code フィールドに載せる)
- **status_code のパース方針**: `std::stoi` は例外を投げる可能性があり、`on_frame_recv_callback` は nghttp2 の C ABI コールバックのため C++ 例外を伝播させると未定義動作になる。したがって `<charconv>` の `std::from_chars` (noexcept) を使い、パース失敗時 (nghttp2 が `:status` を数字文字列としてバリデーション済みなので実発生しないが防御的に) は `status_code = 0` を載せる。範囲は 100-599 の想定だが、範囲外値は `0` に丸めるかそのまま載せるかを実装時に判断 (現状は 3 桁の HTTP status しか受信しないため実発生しない)
- 既存 bindings の意味論 (「非 2xx で SessionClosed は発火しない」) は変更しない。SessionRejected は SessionClosed とは別種の新イベントとして追加され、既存の設計ピンテストは影響を受けない
- サーバー側の `H2Session::reject_session` (bindings 側 API) 呼び出しに対しては SESSION_REJECTED を **発火しない** (サーバーは自分で拒否を決めているため通知不要。SESSION_REJECTED はクライアント側受信経路 = `on_frame_recv_callback` の非 2xx 分岐のみで push する)
- 1xx を挟んだ拒否 (最終応答が `NGHTTP2_HCAT_HEADERS` で通知され本分岐で捕捉されない既知制約) では、既存の「エントリ削除も発生しない」に加えて SESSION_REJECTED も発火しない。既存テスト `test_client_receive_1xx_then_final_response_keeps_session` が守っているピンと同じ扱い

## 完了条件

- `src/bindings/webtransport_h2.h` の `H2EventType` に `SessionRejected` バリアントが追加されている
- `src/bindings/webtransport_h2.h` の `H2Event` に `uint16_t status_code = 0;` フィールドが追加され、「SessionRejected 発火時のみ意味を持つ」旨のコメントが添えられている
- `src/bindings/webtransport_h2.cpp` の非 2xx 応答分岐で `SessionRejected` イベントが push され、`status_code` に実際の HTTP status code (403 等) が載る。`std::from_chars` を使い C++ 例外が nghttp2 の C コールバック境界を越えないこと
- 上記変更に伴い、`src/bindings/webtransport_h2.cpp` の非 2xx 応答分岐にある既存コメント (「SessionClosed は発火しない (黙って削除)」相当) を「SESSION_REJECTED を push した上でエントリを削除する」意味論に更新する
- `src/webtransport/h2.pyi` に `EventType.SESSION_REJECTED` および `Event.status_code: int` プロパティが追加され、docstring に「SessionRejected 発火時のみ HTTP status code。他イベントでは 0」旨が記載され、`ty check` を通る
- `tests/test_webtransport_h2_reject_session.py` に低レベルテストを追加: 非 2xx 応答受信時に `SESSION_REJECTED` イベントが `status_code = 403` (等) 付きで発火することを parametrize で複数 status code (403 / 302 / 500 等、既存 `test_client_non_2xx_reject_removes_session` と同じセット) を横断検証。既存の設計ピンテスト (`test_client_non_2xx_reject_no_session_closed_event`) が引き続き pass することを確認
- `AGENTS.md` のモック禁止に従い、実 `h2_low.Session` 対で Sans-IO テストを追加する (既存 `test_client_non_2xx_reject_removes_session` と同じパターン)
- 全既存テスト pass、`ruff format` / `ruff check` / `ty check` 通過

## 解決方法

- `src/bindings/webtransport_h2.h`:
  - `H2EventType` 列挙に `SessionRejected` を追加
  - `H2Event` 構造体に `uint16_t status_code = 0;  // SessionRejected 発火時の HTTP status code。他イベントでは 0` を追加
- `src/bindings/webtransport_h2.cpp`:
  - `on_frame_recv_callback` の非 2xx 応答分岐 (`wt_sessions_.erase(stream_id)` の直前) に以下を追加:
    ```cpp
    H2Event event;
    event.type = H2EventType::SessionRejected;
    event.session_id = stream_id;
    // std::from_chars を使い C++ 例外を投げない (nghttp2 の C ABI 境界のため)。
    // nghttp2 が :status を数字文字列としてバリデーション済みだが防御的に扱う
    uint16_t code = 0;
    std::from_chars(status_value.data(),
                    status_value.data() + status_value.size(), code);
    event.status_code = code;
    h2_session->push_event(std::move(event));
    ```
  - 上記変更後、既存の非 2xx 分岐コメントを「SESSION_REJECTED を push した上で削除する」意味論に更新
  - `NB_MODULE` の `H2EventType` enum export に `.value("SESSION_REJECTED", H2EventType::SessionRejected)` を追加
  - `H2Event` の nanobind export に `.def_ro("status_code", &H2Event::status_code, "SessionRejected 発火時の HTTP status code。他イベントでは 0")` を追加
  - `<charconv>` を include する
- `src/webtransport/h2.pyi`:
  - `EventType` 列挙に `SESSION_REJECTED` を追加
  - `Event` クラスに `status_code: int` プロパティを追加。docstring に「SessionRejected 発火時のみ HTTP status code (403 等)。他イベントでは 0」旨を記載
- `tests/test_webtransport_h2_reject_session.py`:
  - 新規テスト `test_client_non_2xx_reject_pushes_session_rejected_event`: サーバー役 `h2_low.Session` から `reject_session(session_id, status)` を呼び、クライアント役の Session に `SESSION_REJECTED` イベント (`event.session_id` が該当セッション、`event.status_code == 期待値`) が push されることを parametrize (403 / 302 / 500) で横断確認。既存 `test_client_non_2xx_reject_removes_session` と同じ Sans-IO パターン
  - 既存の設計ピンテスト (`test_client_non_2xx_reject_no_session_closed_event` 等) が引き続き pass することの回帰確認
- 変更対象: `src/bindings/webtransport_h2.h` / `src/bindings/webtransport_h2.cpp` / `src/webtransport/h2.pyi` / `tests/test_webtransport_h2_reject_session.py`
- 変更対象外: `src/webtransport/h2/client.py` (SESSION_REJECTED を高レベルで消費する変更は 0111 のスコープ)
- 変更対象外: `src/webtransport/h2/server.py` (Server 側の拒否 API 追加は 0134 のスコープ)
- 変更対象外: `src/bindings/webtransport_h3.h` / `src/bindings/webtransport_h3.cpp` / `src/webtransport/h3.pyi` (HTTP/3 版の SESSION_REJECTED 追加は 0112 の解決方針次第。本 issue は HTTP/2 のみを扱う)

## 依存関係と関連 issue

- **後続 (0111 の先行必須)**: 0111 (`h2.Client.connect() が非 2xx 拒否で永久ブロックする問題を修正する`) が本 issue のマージを実装着手の前提としている。0111 は本 issue が追加する `SESSION_REJECTED` イベントを高レベル `Client.connect` の while ループで検知して `False` を返す実装のみを行い、`status_code` フィールドは使わない (`False` を返すだけ)。`status_code` を利用する API 追加は本 issue のスコープ外で、将来別 add issue で扱う
- **並列 (0134)**: 0134 (`h2.Server に拒否 API を追加する`) と併せて 0111 の依存を構成する。0133 (本 issue) と 0134 の実装順序は独立可能 (0134 は `h2/server.py` のみで bindings は触らないため衝突しない)
- **関連 (対称性)**: 0112 (`h3.Client.connect()` が 2xx を待たずに True を返す問題を修正する) の解決方針として、HTTP/3 側でも同型の `SESSION_REJECTED` イベントを bindings (`src/bindings/webtransport_h3.cpp`) に追加するかは 0112 の polish 時に判断する。本 issue と 0112 で命名 (`SESSION_REJECTED` / `status_code`) を揃える方針を推奨する
- **関連**: draft-ietf-webtrans-http2-15 §3.2 準拠として SessionClosed 非発火を保つ点で 0111 の polish で合意された方針に沿う
