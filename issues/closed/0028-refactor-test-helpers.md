# WebTransport over HTTP/3 テストの接続ヘルパーを共通化する

- Created: 2026-08-04
- Completed: 2026-08-08
- Branch: feature/refactor-test-helpers
- Polished: 2026-08-08

## 目的

WebTransport over HTTP/3 のテストで使う接続ヘルパー (`_pump` / `_establish_session`) が 4 つのテストファイルで重複しており、セッション確立手順を変更するときに複数箇所の修正が必要になる。共通ヘルパーに集約して保守性を高める。

## 現状

- `tests/test_webtransport_h3_ack_offset.py` の `_pump` (送信データを相手セッションに渡す) と `_establish_session` (h3.Session 同士でセッション確立) が、`tests/test_webtransport_h3_stream_buffer_cleanup.py` / `tests/test_webtransport_h3_stream_state.py` / `tests/test_webtransport_h3_stream_control.py` の同名ヘルパーと実質同一の処理を持つ (stream_state / stream_control の `_pump` と `_establish_session` は stream_buffer_cleanup の分解版とほぼ同一の処理だが、SESSION_READY 複数回発火の検査 (`count <= 1`) は持たない)
- stream_buffer_cleanup 側は 2 セッション確立対応のために `_create_session_pair` / `_accept_session` / `_drain_session_ready` / `_connect_session` / `_establish_two_sessions` に分解されており、ack_offset 側の実装はその機能部分集合 (ただしアサーションは弱い: サーバー側の受理セッション ID の検査 (`session_id >= 0`) と、クライアント側の「SESSION_READY が複数回発火したら累積バグとして失敗させる」検査を持たない)
- ack_offset 版の `_pump` は挙動が異なる: 1 回だけ転送 (単発) で、他の 3 ファイル版はデータが無くなるまで繰り返す (全転送。`for _ in range(64)` の上限付き)。`get_streams_to_send` は 1 回の呼び出しで全てのデータを返すとは限らない (WT_CLOSE_SESSION 等は他のストリームの書き出し後に返ることを 0010 のテスト実装時に確認済み。nghttp3 の実装挙動であり仕様の MUST / SHOULD ではない) ため、全転送版が正しい挙動
- `tests/test_http3_stream_control.py` 等の `http3.Connection` 用 `_pump` は型が異なるため対象外

## 設計方針

- 共通ヘルパー (`_pump` / `_create_session_pair` / `_accept_session` / `_drain_session_ready` / `_connect_session` / `_establish_session` / `_establish_two_sessions`) を `tests/conftest.py` に定義する。利用方法は既存の QUIC ヘルパー (`create_client_server_pair` / `perform_handshake`) と同じ `from conftest import ...` とし、conftest へのヘルパー集約の先例 (git log: QUIC テストの共通ヘルパーを conftest.py に集約する) に合わせる
- `_pump` は全転送版 (データが無くなるまで繰り返す。64 回の上限は既存実装の挙動をそのまま引き継ぐ) に統一する。ack_offset の既存 3 テストは送信データが 1 回の `get_streams_to_send` で全て返る小ささのため、全転送版でも 2 周目以降は空になりループを抜けてアサート結果は不変
- 統一版のアサーションは stream_buffer_cleanup の分解版 (最強) の強度に統一する (サーバー側の `session_id >= 0`、クライアント側の SESSION_READY 複数回発火の検査 (`count <= 1`)、`ready_id == session_id`)。弱い ack_offset 版に合わせない。統一により stream_state / stream_control のテストは SESSION_READY 複数回発火の検査が追加される (強度上昇)
- 共通ヘルパーの命名は既存のテストファイル内の名前 (`_pump` / `_establish_session` 等) をそのまま維持する (アンダースコア始まり。テストファイル内でのみ使うヘルパーであることを表す)
- 重複ヘルパーを持つ 4 ファイル (`tests/test_webtransport_h3_ack_offset.py` / `tests/test_webtransport_h3_stream_buffer_cleanup.py` / `tests/test_webtransport_h3_stream_state.py` / `tests/test_webtransport_h3_stream_control.py`) の重複ヘルパーを削除し、conftest のヘルパーを使う形に書き換える。なお `test_webtransport_h3_stream_control.py` の `test_block_unblock_stream` 内のインライン転送コードは送信成立の検証と結合しているため対象外とする
- 変更対象は `tests/conftest.py` (ヘルパー追加)、上記 4 テストファイル (重複削除と書き換え)
- 0026 も `tests/test_webtransport_h3_stream_buffer_cleanup.py` を変更対象に含むため、実装順序によるマージの競合に注意する

## 完了条件

- 接続ヘルパーの重複が解消され、上記 4 テストファイルの全てが conftest の共通ヘルパーを使う
- 全テストが通る (アサーションは stream_buffer_cleanup の分解版 (最強) に統一したうえで)

## 解決方法

WebTransport over HTTP/3 テストの接続ヘルパーを `tests/conftest.py` に集約した。利用方法は既存の QUIC ヘルパー (`create_client_server_pair` / `perform_handshake`) と同じ `from conftest import ...` とした。

- `tests/conftest.py` に 7 ヘルパー (`_pump` / `_create_session_pair` / `_accept_session` / `_drain_session_ready` / `_connect_session` / `_establish_session` / `_establish_two_sessions`) を追加した。`_pump` はデータが無くなるまで繰り返す全転送版 (64 回の上限付き) に統一し、アサーションは stream_buffer_cleanup の分解版 (最強) の強度に統一した (サーバー側の `session_id >= 0`、クライアント側の SESSION_READY 複数回発火の検査 (`count <= 1`)、`ready_id == session_id`。サーバー側にも SESSION_READY 多重発火の検査を追加して対称化した)
- 重複ヘルパーを持つ 4 ファイル (`tests/test_webtransport_h3_ack_offset.py` / `tests/test_webtransport_h3_stream_buffer_cleanup.py` / `tests/test_webtransport_h3_stream_state.py` / `tests/test_webtransport_h3_stream_control.py`) の重複ヘルパーを削除し、conftest のヘルパーを使う形に書き換えた。`test_webtransport_h3_stream_control.py` の `test_block_unblock_stream` 内のインライン転送コードは送信成立の検証と結合しているため対象外として残した
- ack_offset の `_pump` は単発版だったが、全転送版に統一しても既存 3 テストのアサート結果は不変であることをテスト実行で確認した

テスト本体 (各テスト関数のロジックとアサーション) は変更していない。全テストが通ることを確認済み。
