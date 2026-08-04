# WebTransport over HTTP/3 テストの接続ヘルパーを共通化する

- Created: 2026-08-04
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-test-helpers
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/3 のテストで使う接続ヘルパー (`_pump` / `_establish_session`) が複数のテストファイルで重複しており、セッション確立手順を変更するときに複数箇所の修正が必要になる。共通ヘルパーに集約して保守性を高める。

## 現状

- `tests/test_webtransport_h3_ack_offset.py` の `_pump` (送信データを相手セッションに渡す) と `_establish_session` (h3.Session 同士でセッション確立) が、`tests/test_webtransport_h3_stream_buffer_cleanup.py` の同名ヘルパーと実質同一の処理を持つ
- `tests/test_webtransport_h3_stream_buffer_cleanup.py` 側は 2 セッション確立対応のために `_create_session_pair` / `_accept_session` / `_drain_session_ready` / `_connect_session` / `_establish_two_sessions` に分解されており、`tests/test_webtransport_h3_ack_offset.py` 側の実装はその部分集合になっている
- 両ファイルの `_pump` は挙動が異なる: ack_offset 版は 1 回だけ転送 (単発) で、stream_buffer_cleanup 版はデータが無くなるまで繰り返す (全転送)。`get_streams_to_send` は 1 回の呼び出しで全てのデータを返すとは限らない (WT_CLOSE_SESSION 等は他のストリームの書き出し後に返る) ことを確認済みのため、全転送版が正しい挙動

## 設計方針

- 共通ヘルパー (`_pump` / `_create_session_pair` / `_accept_session` / `_drain_session_ready` / `_connect_session` / `_establish_session` / `_establish_two_sessions`) を `tests/conftest.py` に定義し、両テストファイルから利用する
- `_pump` は全転送版 (データが無くなるまで繰り返す) に統一する。単発転送に依存する既存テストが無いことを確認する
- 既存テストファイル (0013 の `tests/test_webtransport_h3_ack_offset.py`) の重複ヘルパーを削除し、conftest のヘルパーを使う形に書き換える
- 変更対象は `tests/conftest.py` (ヘルパー追加)、`tests/test_webtransport_h3_ack_offset.py` / `tests/test_webtransport_h3_stream_buffer_cleanup.py` (重複削除)

## 完了条件

- 接続ヘルパーの重複が解消され、`tests/test_webtransport_h3_ack_offset.py` と `tests/test_webtransport_h3_stream_buffer_cleanup.py` の両方が conftest の共通ヘルパーを使う
- 全テストが通る (モックなし)
