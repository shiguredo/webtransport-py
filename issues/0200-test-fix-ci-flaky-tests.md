# CI で flaky に失敗するテストを修正する

- Created: 2026-09-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-ci-flaky-tests
- Polished: {YYYY-MM-DD}

## 目的

CI の wheel ワークフローで flaky に失敗するテストが常態化しており、再実行で回避する運用が発生している。失敗テストを根本から修正して CI の信頼性を高める。

## 現状

直近 100 run のうち 13 run が failure で、失敗テストの頻度は次のとおり (2026-09-10 時点)。

| 回数 | テスト |
|---|---|
| 9 | `tests/test_e2e_quic_isolation.py::test_hundred_clients_isolated` |
| 7 | `tests/test_quic_server_routing.py::test_nat_rebinding_keeps_connection` |
| 3 | `tests/test_quic_pacing.py::test_confirmed_write_sets_pacing_deadline` |
| 2 | `tests/test_e2e_quic_stream_abort.py::test_close_stream_sends_reset_sans_io` |
| 1 | `tests/test_quic_pacing.py::test_bulk_send_reports_near_deadline` |
| 1 | `tests/test_quic_stream_control.py::test_extend_max_stream_offset` |
| 1 | `tests/test_quic_error_handling.py::test_connection_close_retransmission_on_receive` |

`tests/test_quic_oversized_datagram.py::test_oversized_datagram_dropped_after_small_one_delivered` (2 回) は別 issue の修正で対応済み。

原因は次の 3 分類。

1. pacing 対応漏れ: ngtcp2 の pacing が有効な場合 `Connection.send()` は期限まで空振り (`nwrite=0`) するが、固定回数の送受信ループで待たずに打ち切るテストがある。`tests/test_quic_conn_stats.py::test_conn_stats_after_handshake` はローカルで 20 回中 19 回失敗する (CI では稀にしか観測されない)
2. 実時間 sleep 依存: 固定の `asyncio.sleep` / `time.sleep` で状態遷移を待つ実ソケット・統合テストがあり、低速な CI ランナーでは待ち時間が不足する
3. CI 負荷に厳しい閾値: 性能・タイミングのアサーションが CI ランナーの負荷で超過する

## 設計方針

- pacing 対応漏れ: 空振り時に `wait_pacing_timeout` で期限まで待って再試行し、目的の状態 (フロー制御残量の減少・イベント到着など) が成立した時点で打ち切る
- 実時間 sleep 依存: 条件成立を上限付きでポーリングする。固定 sleep は待機の上限としてのみ残す
- CI 負荷に厳しい閾値: 外れ値に強い判定 (分位点) に変更し、波及の検出力を維持する
- pacing の期限が固定のスリープ上限を超える場合は、期限まで待つか上限を緩和する

## 完了条件

- 対象の flaky テストがローカルで 20 回連続して通過すること
- 全テストが通過すること
- CI で通過すること
