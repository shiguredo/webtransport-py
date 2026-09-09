# WebTransport over HTTP/3 の低レベル API テストを分割する

- Created: 2026-08-08
- Completed: 2026-09-09
- Branch: feature/refactor-split-e2e-h3-tests
- Polished: 2026-09-07

## 目的

`tests/test_e2e_webtransport_h3.py` が 3,554 行に肥大化し、低レベル API クライアント (`_LowLevelClient`) とそれを利用するリセット系・データグラム系テスト群が高レベル API (Client / Server) のテストと同居して保守性が低下している。低レベル API 構成のテストを別ファイルへ分割して可読性と保守性を高める。

## 現状

- `tests/test_e2e_webtransport_h3.py` は 3,554 行で、高レベル API (Client / Server) のテストに加え、同一 QUIC 接続上に複数セッションを確立する
  低レベル API 構成 (`_LowLevelClient` クラス、292 行) とそれを利用するテスト (STREAM_RESET 系・RESET_STREAM_AT・不正なセッション ID のデータグラム等)
  が同居している
- `_LowLevelClient` は `quic.Connection` + `h3.Session` を直接構築する接続手順 (ハンドシェイク・SETTINGS 待ち・制御ストリームのバインド) と、複数セッション確立・ストリーム操作・パケット保留などのヘルパーを含む

## 設計方針

- `tests/test_e2e_webtransport_h3.py` から低レベル API 構成のテスト群と `_LowLevelClient` を、新規ファイル `tests/test_e2e_webtransport_h3_low_level.py`
  へ分割する (e2e テストの命名慣例 `test_e2e_<プロトコル>[_<トピック>].py` に従う。`test_e2e_quic_advanced.py` 等が前例)
- 移動対象は次に限定する
  - `_LowLevelClient` クラス
  - 低レベルテスト専用のヘルパー: `_ResetTestServerInfo` / `_start_reset_test_server` / `_cleanup_reset_test_server` / `_SessionClosedServerInfo` / `_start_session_closed_server` (低レベルテスト専用のため、残すと本ファイルに未使用コードが残る)
  - 低レベル API を使うテスト 8 件: `test_stream_reset_second_session_id` / `test_stream_reset_at_recovers_session_id`
    / `test_stream_reset_before_data_received_minus_one` / `test_stream_reset_connect_stream_session_id`
    / `test_connect_stream_reset_notifies_session_closed` / `test_connect_stream_fin_notifies_session_closed`
    / `test_datagram_invalid_session_id_closes_connection` / `test_datagram_closed_session_id_discarded`
  - `test_datagram_closed_session_id_discarded` が使う `from conftest import _encode_wt_datagram` も新ファイルへ移す (高レベルテストからは使われていない)
- 本ファイルに残すもの: 高レベル API (Client / Server) だけを使うテスト。低レベルブロック周辺では `test_server_resets_client_connect_stream_closes_session`
  / `test_server_fin_closes_client_session` / `test_datagram_invalid_session_id_closes_connection_client` / `test_server_stop_delivers_connection_close`
  / `test_client_open_stream_after_session_close_returns_minus_one` / transport parameter 検証 4 件 / `test_connect_timeout_on_blackhole`、
  および高レベルテスト専用のヘルパー `_make_config_missing_transport_params` が該当する (名前から低レベルテストと誤認しないこと)
- テストの動作・アサーションは変更しない (純粋なリファクタリング。`### misc` 相当の変更として `[UPDATE]` で CHANGES.md に記載する)
- `_LowLevelClient` は実ソケットを使う e2e 接続のための別系統であり、conftest.py に集約済みの Sans-IO 用接続ヘルパー (`_pump` / `_establish_session`
  / `_establish_two_sessions` 等) とは置き換え対象ではない。新ファイルでは、`_pump` / `_establish_session` / `_establish_two_sessions` / `_drain_events`
  など conftest.py のモジュールレベル名と衝突する名前を定義しないこと

## 完了条件

- 上記の低レベル API を使うテスト・ヘルパーが `tests/test_e2e_webtransport_h3_low_level.py` へ移動し、`tests/test_e2e_webtransport_h3.py` が高レベル API のテストに集約される
- 全テストが通る

## 解決方法

- `tests/test_e2e_webtransport_h3_low_level.py` を新設し、`_LowLevelClient` / `_ResetTestServerInfo` / `_start_reset_test_server` / `_cleanup_reset_test_server` / `_SessionClosedServerInfo` / `_start_session_closed_server` と低レベル API を使う 8 テスト、`from conftest import _encode_wt_datagram` を移動した
- `tests/test_e2e_webtransport_h3.py` は高レベル API のテストだけを残し、モジュール docstring を分割後の構成に合わせて更新した
- `CHANGES.md` の `### misc` に `[UPDATE]` エントリを追加した
- 移動はトップレベル定義単位で行い、テスト本体・アサーション・デコレータは変更していない。`ruff check` / `ruff format --check` と全 976 テストの通過を確認した
