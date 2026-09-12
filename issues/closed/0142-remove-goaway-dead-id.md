# HTTP/3 の goaway の死んだ id 引数を削除する

- Created: 2026-09-03
- Completed: 2026-09-12
- Branch: feature/remove-goaway-dead-id
- Polished: {YYYY-MM-DD}

## 目的

`Http3Connection::goaway` の死んだ id 引数を削除し、公開 API を実態に合わせる (公開 API の破壊的変更は CODEBASE.md で容認されている)。

## 現状

- **HTTP/3 の goaway(id) の id 引数が無視される**: `src/bindings/http3.cpp` の `Http3Connection::goaway` は引数 id を受け取るが `nghttp3_conn_shutdown` には渡さず、GOAWAY ID は nghttp3 が内部算出する (サーバーは受信最大双方向ストリーム ID を基に算出、クライアントは 0)。nghttp3 には GOAWAY ID を指定する API がないため「意味のあるものにする」には nghttp3 の改修が必要であり対象外とする
- 0124 (死にコード削除) の対象に当該引数は含まれず重複しない

## 設計方針

- `src/bindings/http3.h` の宣言、`src/bindings/http3.cpp` の実装とバインディング定義 (`nb::arg("id")`) から id 引数を削除する。pyi は nanobind 生成物のため手編集せず、bindings 側の定義変更から再生成する (0133 の確立規約)
- 既存呼び出し側を更新する。`tests/test_http3_message_ext.py` と `tests/test_http3_stream_state.py` の `goaway(0)`、`tests/prop_http3.py` の `prop_goaway_arbitrary_id` (任意 ID 堅牢性テストであり引数削除で存在意義がなくなるため更新または削除)
- 0132 と同一ファイル (`src/bindings/http3.cpp`) を変更するため、並行着手する場合は順序調整または rebase 前提とする

## 完了条件

- 引数なしの `goaway()` で GOAWAY が送出され、graceful shutdown が従来通り動作するテストがある
- 上記の既存呼び出し側テストが更新され、既存の全テストが通る

## 関連 issue

- `issues/closed/0123-refactor-http-event-details.md` — 分離元 (goaway 項目を移管)
- `issues/0124-remove-dead-code-cpp.md` — 当該引数は対象外であり重複しない
- `issues/0132-add-http3-bindings-test-force-close.md` — 同一ファイルを変更するため順序調整

## 解決方法

`Http3Connection::goaway` から無視されていた `id` 引数を削除した。

- `src/bindings/http3.h` の宣言を `void goaway()` に変更し、GOAWAY ID は nghttp3 が内部算出する (nghttp3 に ID を指定する API が無い) ことを docstring に明記した
- `src/bindings/http3.cpp` の実装シグネチャとバインディング (`nb::arg("id")` を削除し `nb::sig("def goaway(self) -> None")` に変更) を更新した。型スタブは `make develop` で再生成した
- 呼び出し側を更新した。`tests/test_http3_message_ext.py` の 2 箇所と `tests/test_http3_stream_state.py` の 1 箇所を `goaway()` に変更した
- `tests/prop_http3.py` の `prop_goaway_arbitrary_id` は任意 ID の堅牢性テストであり引数削除で存在意義が無くなるため、`prop_goaway_without_control_stream_is_noop` に置き換えた (コントロールストリーム未バインドの `goaway()` が何もせず、接続状態と必要ストリーム一覧が変わらないことを確認する)
- `skills/webtransport-py/SKILL.md` の `http3.Connection` のメソッド一覧を `goaway()` に、`close_stream` の既定値を 0x0100 に更新した (0141 の追従)

`uv run pytest tests/ --timeout=30` の 1089 件が全て通る。
