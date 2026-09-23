# h2 の受理前 END_STREAM の保留状態をセッション情報へ移動して簿記をなくす

- Created: 2026-09-23
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-h2-pre-accept-end-stream-state
- Polished: {YYYY-MM-DD}

## 目的

0253 で追加した受理前 END_STREAM の保留状態は `H2Session` のセッション単位の集合 `pending_pre_accept_end_stream_session_ids_` で持ち、エントリ削除・終了フラグを立てる全経路 (約 7 箇所) とムーブコンストラクタ / ムーブ代入で除去・転送する簿記が必要になっている。これらの除去は現行の到達経路では挙動に現れず、経路追加のたびに漏れのリスクが生じる。状態を `WtSessionInfo` に持たせれば、エントリの破棄と同時に消えるため簿記が不要になる。

## 現状

- `H2Session` は `std::set<int32_t> pending_pre_accept_end_stream_session_ids_` を持ち、`H2Session::handle_end_stream` で記録し、`H2Session::accept_session` / `H2Session::send_datagram` / `H2Session::drain_session` で参照する
- 除去は `H2Session::reset_stream_for_malformed_capsule` / `H2Session::handle_wt_close_session` / `H2Session::reject_session` (2 分岐) / `H2Session::close_session` / `H2Session::on_stream_close_callback` / `H2Session::terminate_pre_accept_end_stream_session` / `H2Session::accept_session` の早期 return に散在する
- ムーブコンストラクタ / ムーブ代入にも転送を追加済み (0253 のレビュー対応)
- 状態の除去は外部から観測できず、退行をテストで検出しづらい

## 設計方針

- `WtSessionInfo` に `bool pre_accept_end_stream = false;` を追加し、`H2Session::handle_end_stream` の検知で立てる。`H2Session::accept_session` / `H2Session::send_datagram` / `H2Session::drain_session` は `get_wt_session` 経由でフラグを参照する
- 集合メンバーと全除去箇所、ムーブの転送を削除する (`WtSessionInfo` は `wt_sessions_` のムーブで一緒に移動する)
- フラグが `is_terminated` と意味が異なる点 (受理前の終了検知専用で、`process_capsules` の蓄積破棄を起こさない) をコメントで明記する
- テスト専用アクセサ `_test_has_pre_accept_end_stream(session_id) -> bool | None` (例) を追加し、検知で立ち、終了・エントリ削除の各経路で消えることを直接固定する (CODEBASE.md の細粒度の観測方針。既存の `_test_pending_header_count` 等と同じ配置)
- 変更対象: `src/bindings/webtransport_h2.cpp` / `.h`、`src/webtransport/webtransport_ext/h2.pyi` (再生成)、`tests/test_webtransport_h2_end_stream.py` / `tests/test_webtransport_h2_datagram.py`
- `CHANGES.md` は現時点では変更しない (CODEBASE.md の指示)

## 完了条件

- `pending_pre_accept_end_stream_session_ids_` とその除去・転送がなくなり、`WtSessionInfo` のフラグで同じ挙動になる
- 受理前 END_STREAM の終了 (クリーン / WT_CLOSE_SESSION / 切り詰め / 拒否) と抑止 (`send_datagram` / `drain_session`) の既存テストがすべてそのまま通過する
- テスト専用アクセサで、検知後のフラグが終了・エントリ削除の各経路で `False` / `None` に戻ることを固定する
- フラグを立てる処理を外すとテストが失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- 受理前 END_STREAM の挙動変更 (0253 で実装済み。0254 / 0255 は別 issue)
