# WebTransport over HTTP/3 高レベル Server にセッション拒否 API を追加する

- Created: 2026-08-27
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-server-reject-session-api
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 3.2 準拠のセッション拒否手段を `h3.Server` に追加する。`h2.Server` の `on_session_request` コールバック (closed/0134) と対称の API を提供し、アプリが CONNECT 受信時に非 2xx で拒否できるようにする。

## 現状

- `src/webtransport/h3/server.py` の `Server._process_webtransport_events` は SESSION_READY 受信時に `meets_transport_param_requirements` の検証を通過した後、`client.webtransport_session.accept_session(webtransport_event.session_id)` を呼んでおり、アプリが介入して拒否する経路がない
- draft-ietf-webtrans-http3-16 Section 3.2 は 403 (Origin 検証失敗、SHOULD) / 405 (対象リソース未対応、SHOULD) / 3xx (redirect) の応答を挙げており、2xx で accept される。拒否手段がないと 403 / 405 の拒否シナリオをアプリで実現できない
- `h2.Server` は closed/0134 で `on_session_request` コールバック API を提供済み
- 低レベル `h3.Session.reject_session(session_id, status_code)` は既に実装済み (`src/webtransport/h3.pyi` の `Session.reject_session`、`src/bindings/webtransport_h3.cpp` の `H3Session::reject_session` および `.def("reject_session", ...)` バインド)。Origin 検証の 403 拒否経路 (verify_origin) からも既に呼ばれている
- `H3Event` (`src/bindings/webtransport_h3.h`) は現状 `headers` フィールドを持たず、SESSION_READY push 時にも受信 CONNECT ヘッダーを積んでいない。コールバックにヘッダーを渡すには先行して bindings を拡張する必要がある

## 設計方針

- `src/bindings/webtransport_h3.h` の `H3Event` 構造体に `headers` フィールド (`std::vector<std::pair<std::string, std::string>>` 相当) を追加する
- `src/bindings/webtransport_h3.cpp` の SESSION_READY push 時に受信 CONNECT ヘッダーを `H3Event.headers` に載せる。`nb::def_ro("headers", ...)` で公開する
- `src/webtransport/h3.pyi` の `Event` に `headers: list[tuple[str, str]]` プロパティを追加する
- `src/webtransport/h3/server.py` の `Server` に `on_session_request` コールバックを `h2.Server` と実装レベルで対称に追加する
  - シグネチャ: `Callable[[int, list[tuple[str, str]], tuple[str, int]], Awaitable[int | None]]` (session_id, headers, addr)
  - addr の型は h3 側の既存コールバック (`on_session_ready` など) が使う `tuple[str, int]` に揃える。`h2.Server.on_session_request` は `tuple[object, ...]` 型を渡しており厳密な同一型ではないが、h3 側は `_normalize_addr` で `(host, port)` の 2 要素に正規化する既存慣行に合わせる方が自然
  - 戻り値: `None` または `200` 以上 `600` 未満の `int` を受け入れる
  - 戻り値検証は `ValueError` のみ (`bool` は弾く / 非 int は弾く / 範囲外は弾く。`TypeError` は使わない。`h2.Server` 実装の検証ロジックと同一)
- SESSION_READY 分岐で `on_session_request` を呼び、戻り値に応じて分岐する
  - `None` または `200-299`: `accept_session(session_id)` を呼び、`on_session_ready` を発火する
  - `300-599`: `reject_session(session_id, status)` を呼び、`on_session_ready` は発火しない
- Origin 検証は既存の `h3.Config.allowed_origins` に委ねる (本 issue では手を加えない)

## 完了条件

- `H3Event` に `headers` フィールドが追加され、bindings と pyi で公開される
- `h3.Server.on_session_request` が `h2.Server` と実装レベルで同一シグネチャ・同一検証で動作する
- `None` / `200-299` で `accept_session` が呼ばれ、`on_session_ready` が発火する
- `300-599` で `reject_session` が呼ばれ、`on_session_ready` は発火しない
- `bool` / 非 int / 範囲外 (200 未満 / 600 以上) で `ValueError` が送出されるテストがある
- accept 経路が既存 e2e テストと互換
- 拒否経路の e2e テストが追加される (h2 側の拒否テストを参考にする)
- 既存の全テストが通る

## 関連 issue

- `issues/closed/0134-add-h2-server-reject-session-api.md` — h2 側の対称実装 (シグネチャ・検証・完了条件の参考)
