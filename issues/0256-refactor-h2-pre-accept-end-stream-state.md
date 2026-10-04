# h2 の受理前 END_STREAM の保留状態をセッション情報へ移動して簿記をなくす

- Created: 2026-09-23
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-h2-pre-accept-end-stream-state
- Polished: 2026-10-04

## 目的

0253 で追加した受理前 END_STREAM の保留状態は `H2Session` のセッション単位の集合 `pending_pre_accept_end_stream_session_ids_` で持ち、エントリ削除を伴う経路 (4 箇所)・エントリを残したまま `is_terminated` を立てる経路 (3 箇所)・`accept_session` の防御的除去 (1 箇所) と、ムーブコンストラクタ / ムーブ代入での転送 (2 箇所) という簿記が必要になっている。エントリ削除を伴う経路の除去はエントリ破棄と重複しており、現行の到達経路では挙動に現れないため、経路追加のたびに漏れのリスクが生じる。状態を `WtSessionInfo` に持たせれば、エントリの破棄と同時にフラグも消えるため、エントリ削除を伴う経路の除去とムーブの転送は不要になる。エントリを残したまま終了を学習する 3 経路だけは `is_terminated = true` と同一箇所でフラグを落とす必要が残るが、既存の `is_terminated` の代入箇所と並置でき、集合の除去・転送という別系統の簿記は消える。

## 現状

- `H2Session` は `std::set<int32_t> pending_pre_accept_end_stream_session_ids_` を持ち、`H2Session::handle_end_stream` で記録し、`H2Session::accept_session` / `H2Session::send_datagram` / `H2Session::drain_session` で参照する
- 集合の除去は次の経路に散在する
  - エントリ削除を伴う経路 (4 箇所): `H2Session::handle_wt_close_session` / `H2Session::reject_session` の非 2xx 分岐 / `H2Session::on_stream_close_callback` / `H2Session::terminate_pre_accept_end_stream_session` のクリーン分岐
  - エントリを残したまま `is_terminated` を立てる経路 (3 箇所): `H2Session::reset_stream_for_malformed_capsule` / `H2Session::reject_session` の 2xx 分岐 / `H2Session::close_session`
  - 防御 (1 箇所): `H2Session::accept_session` の早期 return (エントリ削除と `is_terminated` の両方のケースを包含)
- ムーブコンストラクタ / ムーブ代入にも転送を追加済み (0253 のレビュー対応)
- 状態の除去は外部から観測できず、退行をテストで検出しづらい

## 設計方針

- `WtSessionInfo` に `bool pre_accept_end_stream = false;` を追加し、`H2Session::handle_end_stream` の検知 (現在の `insert` 箇所) で立てる。`H2Session::accept_session` / `H2Session::send_datagram` / `H2Session::drain_session` は `get_wt_session` 経由でフラグを参照する。`accept_session` は `is_terminated` の早期 return を通過した後のみフラグを確認する (終了処理済みのセッションで再終了しない)
- 集合メンバー、エントリ削除を伴う経路の除去 (上記 4 箇所)、`accept_session` の防御的除去、ムーブコンストラクタ / ムーブ代入の転送を削除する (`WtSessionInfo` は `wt_sessions_` のムーブで一緒に移動する。`accept_session` の早期 return は、エントリ削除ならフラグごと消え、`is_terminated` なら上記 3 経路が `is_terminated` を立てた時点でフラグも落ちているため不要になる)
- エントリを残したまま `is_terminated` を立てる 3 経路 (`reset_stream_for_malformed_capsule` / `reject_session` の 2xx 分岐 / `close_session`) では、`is_terminated = true` と同一箇所でフラグを false に戻す
- フラグが `is_terminated` と意味が異なる点 (受理前の終了検知専用で、`process_capsules` の蓄積破棄を起こさない) をコメントで明記する
- テスト専用アクセサ `_test_has_pre_accept_end_stream(session_id) -> bool | None` (例) を追加する (CODEBASE.md の細粒度の観測方針。既存の `_test_pending_header_count` 等と同じ配置で、`src/webtransport/webtransport_ext/h2.pyi` を再生成する)。戻り値は: エントリ無し → `None`、エントリあり → フラグ値 (`True` / `False`)
- 変更対象: `src/bindings/webtransport_h2.cpp` / `.h`、`src/webtransport/webtransport_ext/h2.pyi` (再生成)、`tests/test_webtransport_h2_end_stream.py` / `tests/test_webtransport_h2_datagram.py`
- `CHANGES.md` は現時点では変更しない (CODEBASE.md の指示)

## 完了条件

- `pending_pre_accept_end_stream_session_ids_` の集合メンバー、エントリ削除を伴う経路での除去、`accept_session` の防御的除去、ムーブコンストラクタ / ムーブ代入での転送がなくなり、`WtSessionInfo` のフラグで同じ挙動になる
- 受理前 END_STREAM の終了 (クリーン / WT_CLOSE_SESSION / 切り詰め / 拒否) と抑止 (`send_datagram` / `drain_session`) の既存テストがすべてそのまま通過する
- テスト専用アクセサで、検知前に `False` → 検知後に `True` になり、受理 (クリーン / 切り詰め / 不正カプセル)・WT_CLOSE_SESSION の遅延処理・拒否 (非 2xx)・`close_session` / `reject_session` の 2xx 送出・ストリームクローズの各経路を経た後に `False` または `None` になる (`True` のまま残らない) ことを固定する
- フラグを立てる処理を外すとテストが失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- 受理前 END_STREAM の挙動変更 (0253 で実装済み。0254 / 0255 は別 issue)
- h3 側の同種の保留集合 (`H3Session::pending_pre_accept_fin_session_ids_`) の置き換え (本 issue は h2 限定)
