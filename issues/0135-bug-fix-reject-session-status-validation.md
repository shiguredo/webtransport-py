# WebTransport over HTTP/2 の reject_session が不正な status code を無検証で送出する問題を修正する

- Created: 2026-08-23
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-reject-session-status-validation
- Polished: 2026-08-24

## 目的

`h2_low.Session.reject_session(session_id, status_code)` (bindings 直接呼び出し経路) が status_code を無検証で `:status` として送出しており、HTTP status code として不正な値を渡すとクライアント側の挙動が壊れ、「非 2xx 拒否で SessionClosed は発火しない」設計ピン (draft-ietf-webtrans-http2-15 Section 3.2「A WebTransport session is established when the server sends a 2xx response」) に反する挙動が発生する。入力検証を追加して誤用時に例外とし、設計ピンを守れない状況を未然に防ぐ。高レベル API (`src/webtransport/h2/server.py`) の経路は 200-599 の範囲を検証済み (closed 0134) であり、本 issue は残る bindings 直接経路が対象である。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::reject_session` は `std::to_string(status_code)` で `:status` を生成し、`nghttp2_submit_response` に渡す。値の検証は一切ない
- クライアント側の nghttp2 は `:status` を「3 桁数字 (先頭 '1'-'9')」かつ 101 以外に制限する (このため 2 桁以下・4 桁以上の値と 101 はクライアントでヘッダーエラーになる)。600-999 は受理され、非 2xx 分岐の `SESSION_REJECTED` で 0 に丸められる (closed 0133)
- 実測 (Sans-IO テストでサーバー役 `reject_session` に 99 / 100 / 199 を渡し、クライアント役を受信): クライアントでは `SESSION_CLOSED` が発火する。「非 2xx で SessionClosed は発火しない」を守る設計ピン (`tests/test_webtransport_h2_reject_session.py` の `test_client_non_2xx_reject_no_session_closed_event`) が誤用パスで破られる
- reject_session 経由の 1xx (`:status: 100` 等) は END_STREAM 付きで送出されるため、クライアントの nghttp2 は HTTP メッセージングエラーとしてストリームエラーとし、`SESSION_CLOSED` が発火してエントリも削除される (「1xx が中間応答として無視され最終応答を待ち続ける」のは、END_STREAM なしの 1xx をワイヤ注入した場合のみ)
- 注意: なお `src/bindings/webtransport_h3.cpp` の `H3Session::reject_session` にも同種の無検証があるが、本 issue は H2 側のみを対象とする (H3 側は別途検討)

## 設計方針

- サーバー側 API (bindings / Python) のシグネチャは変えず、**検証と例外化** を追加する:
  - `reject_session(session_id, status_code)` が `status_code < 200 || status_code >= 600` なら `std::invalid_argument` (nanobind のデフォルト翻訳で ValueError) を投げる。許容範囲は 200-599 で、1xx と 3 桁未満・4 桁以上・600 以上を明示的に拒否する (1xx は中間応答であり、最終応答としての `reject_session` の意味論が成立しないため。`std::invalid_argument` の実行パスでの throw は本リポジトリの既存例にないが、nanobind の標準翻訳を利用する)
  - 200-299 は従来どおり許容する (現行実装は 2xx でエントリ残留 + is_terminated の特殊挙動を持ち、セッション確立の意味論を持つ 201 応答の既存テストがある。201 系テストは open 中 0104 (2xx 全般の確立化) と関係するため、実装順序を考慮する)
- 例外化の位置は関数冒頭 (接続ガードの直後) に置き、`nghttp2_submit_response` を含む副作用の前に状態を汚さないこと。`parse_webtransport_init` 失敗時に呼ばれる内部経路 (400 固定) は許容範囲内であり、例外は発生しない
- docstring は bindings 側の `nb::def` (src/bindings/webtransport_h2.cpp の reject_session の doc) に「status_code は 200-599 (実質 300-599 用)。それ以外は ValueError」を明記する (`src/webtransport/h2.pyi` は nanobind が生成する成果物のため手編集しない。closed 0133 / 0134 と同じ扱い)
- テスト: `test_webtransport_h2_reject_session.py` に範囲外 status_code (99 / 100 / 199 / 600 / 999) で ValueError が投げられ、ワイヤに `:status` が送出されないことを検証するテストを追加する
- 既存の `test_client_non_2xx_reject_pushes_session_rejected_event` は 600 / 700 の「SESSION_REJECTED (status_code 0)」をピン留めしているが、本修正後は 600 以上が ValueError になるため衝突する。同テストを 403 / 302 / 500 等の許容範囲内のパラメータに限定し、600 以上のケースはワイヤ注入方式で 0 丸めの挙動を検証する形に更新する

## 完了条件

- `reject_session` が許容範囲外の status_code (例: 99 / 100 / 101 / 199 / 600 / 999) で ValueError となり、クライアント側の「SessionClosed 非発火」設計ピンを含む既存挙動が壊れないこと
- 許容範囲外のテストが追加され pass すること (range 検証)。既存の 600/700 ピン (test_client_non_2xx_reject_pushes_session_rejected_event) は許容範囲内のパラメータ + ワイヤ注入の 0 丸め検証に更新される
- bindings 側の docstring に制約が明記され、`ty check` が通ること
- 既存の 2xx (201 等) の挙動が変わらないこと (既存テスト pass。0104 実装後はその更新後のテストで担保)
- 全テスト pass、`ruff format` / `ruff check` / `ty check` 通過
