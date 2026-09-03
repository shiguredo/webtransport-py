# HTTP/2・HTTP/3 の受信トレーラと 1xx を Headers から区別できるようにする

- Created: 2026-09-03
- Completed: {YYYY-MM-DD}
- Branch: feature/add-http-trailers-informational
- Polished: {YYYY-MM-DD}

## 目的

RFC 9114 Section 4.1 (HTTP Message Framing) の 1xx (interim response) セマンティクスとトレーラを扱えるようにし、受信トレーラ・1xx レスポンスを通常の HEADERS イベントから区別できるようにする。

## 現状

- **受信トレーラ・1xx レスポンスが Headers イベントと区別できない**: `src/bindings/http2.cpp` の `on_frame_recv_callback` は HEADERS を `frame->headers.cat` を無視して通常の Headers イベントとして積む。`src/bindings/http3.cpp` は `end_trailers_cb` が `end_headers_cb` に委譲し、1xx も同一の Headers イベントになる
- 送信側 (`Http2Connection::submit_trailer`、`Http3Connection::submit_trailers` / `submit_info`) は実装済みであり、不足は受信側の識別のみ

## 設計方針

- h2 / h3 双方に新 `EventType` 値 `INFORMATIONAL` (1xx) と `TRAILERS` を追加する
  - h2 は `frame->headers.cat` と `:status`・END_STREAM で判定する (1xx の `:status` を持つ HEADERS は `INFORMATIONAL`、`:status` を持たない終端 HEADERS は `TRAILERS`、それ以外は最終応答として `HEADERS`)
  - h3 は `end_trailers_cb` の `end_headers_cb` への委譲をやめて `TRAILERS` を積み、`end_headers_cb` では `:status` が 1xx の場合に `INFORMATIONAL` を積む
- 新値は末尾に追加し、既存値に割り当てられた数値リテラルを変えない (0133 の確立規約)。pyi は nanobind 生成物のため手編集せず、bindings 側の定義変更から再生成する
- 0130 の `Error` 追加と双方が末尾追加になる場合は、マージ順で番号を調整する
- 0125 (同一ファイルの送信経路の共通化) とは対象関数が異なり重複しない。0129 と同一ファイル (`src/bindings/http2.cpp`) を変更するため、並行着手する場合は順序調整または rebase 前提とする

## 完了条件

- 受信トレーラが `TRAILERS`、1xx が `INFORMATIONAL`、最終レスポンスが `HEADERS` として区別観測できるテストがある (h2 / h3)
- 既存の全テストが通る

## 関連 issue

- `issues/closed/0123-refactor-http-event-details.md` — 分離元 (トレーラ・1xx 項目を移管)
- `issues/0130-add-http3-error-code-notification-api.md` — `Http3EventType` への末尾追加で競合するためマージ順を調整する
- `issues/0125-refactor-duplicated-code.md` — 同一ファイルの送信経路が対象であり重複しない
- `issues/0129-add-http2-bindings-test-force-close.md` — 同一ファイルを変更するため順序調整
