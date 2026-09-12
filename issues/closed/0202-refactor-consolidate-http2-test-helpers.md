# HTTP/2 テストの _exchange_settings / _pump を conftest.py に集約する

- Created: 2026-09-11
- Completed: 2026-09-12
- Branch: feature/refactor-consolidate-http2-test-helpers
- Polished: {YYYY-MM-DD}

## 目的

`http2.Connection` 同士の SETTINGS 交換と送受信ポンプを行うヘルパーが、複数のテストファイルに同一実装で重複している。同一ヘルパーが複数箇所にあると、http2 バインディングの送受信仕様が変わった際に修正が複数箇所必要になり、修正漏れのリスクになる。closed/0081 と同じ方針で `tests/conftest.py` に集約し、1 箇所にする。

## 現状

- `tests/test_http2.py` / `tests/test_http2_message_ext.py` / `tests/test_http2_session_control.py` / `tests/test_http2_session_state.py` の 4 ファイルに、それぞれローカルな `_exchange_settings(client: http2.Connection, server: http2.Connection)` と `_pump(src: http2.Connection, dst: http2.Connection)` がある
- 4 ファイルの実装は同一で、SETTINGS 交換ループと、送信データが無くなるまで `send()` → `receive()` を繰り返すポンプ
- `tests/conftest.py` には `_h2_pump` (型を `http2.Connection | h2.Session` に拡張済み) と `_create_h2_session_pair` / `_create_h2_http2_pair` があり、横断ヘルパーの置き場として集約が進んでいる
- closed/0073 と closed/0028 でテストヘルパーの conftest 集約の先例がある

## 設計方針

- `tests/conftest.py` に `http2.Connection` のクライアント・サーバーペアを作成して SETTINGS 交換まで行うヘルパー (例: `_create_http2_connection_pair`) を追加する。ポンプは既存の `_h2_pump` を再利用する
- 4 ファイルのローカル `_exchange_settings` / `_pump` を削除し、conftest のヘルパーと `_h2_pump` の import に置き換える
- テストの検証内容は変えない純粋なリファクタリングとする
- 変更対象: `tests/conftest.py` / 上記 4 テストファイル / `CHANGES.md` の `### misc` へのエントリ

## 完了条件

- `tests/conftest.py` に SETTINGS 交換ヘルパーが 1 箇所だけ存在すること
- 4 テストファイルのローカル `_exchange_settings` / `_pump` が削除され、conftest のヘルパーと `_h2_pump` の import に切り替わっていること
- 全テストが通ること

## 解決方法

`tests/conftest.py` に `_exchange_http2_settings` と `_create_http2_pair` を追加し、4 テストファイルのローカルヘルパーを削除した。

- `_exchange_http2_settings`: `http2.Connection` 同士の SETTINGS 交換。既存の `_create_h2_http2_pair` と同じループに収束判定の `AssertionError` を加えたもの
- `_create_http2_pair`: `http2.Connection` のクライアント・サーバーペア作成と SETTINGS 交換 (`_create_h2_http2_pair` の http2 同士版)
- `_h2_pump` は既存のものを再利用し、`_create_h2_http2_pair` も `_exchange_http2_settings` を使う形に寄せた
- `tests/test_http2.py` / `test_http2_message_ext.py` / `test_http2_session_control.py` / `test_http2_session_state.py` からローカルの `_exchange_settings` / `_pump` / `_create_connection_pair` を削除し、呼び出しを `_exchange_http2_settings` / `_h2_pump` / `_create_http2_pair` に置き換えた
- 各ファイルの `conftest` からの import を実際に使う名前だけに揃えた

テストの検証内容は変更していない。`uv run pytest tests/ --timeout=30` の 1058 件が全て通ることを確認した。
