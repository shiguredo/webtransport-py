# 確立済み Client / Server ペアへの API 呼び出し系列を検証するステートフル PBT (RuleBasedStateMachine) を導入する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/test-add-stateful-pbt-for-connect-session-pair
- Polished: {YYYY-MM-DD}

## 目的

現在の `tests/prop_*.py` 12 ファイル・136 関数のうち「実装の性質を検証」しているのは約 15 件のみで、setter 往復 33 件・no-crash のみ 76 件・`@given` 無しの単体テスト 12 件が大半。最大の構造的欠陥は「136 関数すべてが新規未接続オブジェクト 1 個または 1 回のハンドシェイクに対する操作で、確立済みペアに対する操作系列を駆動するものが 1 つも無い」こと。この結果、issue 0145 の QPACK ブロック中 DATA pipeline による SIGABRT (3 ステップの状態遷移で発火) は 136 個の property test で見逃した。ステートフル PBT を導入して回帰ピンにする。

## 現状

- `tests/prop_*.py` の全 9 ファイル 136 関数の分類:
  - setter 往復: `prop_quic.py` 7、`prop_http3.py` 5、`prop_http2.py` 4、`prop_webtransport_h2.py` 9、`prop_webtransport_h3.py` 4 = 計 29 件
  - no-crash のみ: 各ファイルの `prop_*_arbitrary` 系、`prop_isolation_*` の大半 = 計 76 件
  - `@given` 無しの単体テスト: `prop_webtransport_h2.py` 5、`prop_webtransport_h3.py` 7 = 計 12 件
  - 実装の性質を検証: `prop_http2_roundtrip.py` 5、`prop_quic_handshake.py` 3、UTF-8 切り詰め roundtrip 各 1、datagram wire format 数件 = 計 15 件
- `RuleBasedStateMachine` の使用は 0 件 (grep 済み)
- 確立済みペアに対する API 呼び出し系列を検証する PBT は存在しない
- issue 0145 のクラッシュは `headers → DATA フレーム → QPACK エンコーダー投入` の 3 ステップの状態遷移で発火し、既存 property test では捕捉できなかった構造的欠陥の存在証明

## 設計方針

- `hypothesis.stateful.RuleBasedStateMachine` を h3 / h2 / quic の各 Sans-IO ペアに対して実装する
- rule: API 呼び出し (`open_stream` / `send_stream_data` / `close_stream` / `reset_stream` / `close_session` 等) + ワイヤ注入 (`receive_stream_data` を任意分割・任意順序で)
- invariant: `abort / SIGABRT を起こさない`、`SessionClosed が 1 回のみ発火`、`session_ids が単調に整理される`、`sent bytes == received bytes` 等
- 各層で「回帰ピン化したい既知バグ」を name 付きプロパティとして明示する:
  - `prop_h3_qpack_blocked_pipelined_data_no_abort` (issue 0145 の回帰ピン)
  - `prop_h2_flow_control_credit_never_self_close` (issue 0156 の回帰ピン)
  - `prop_h2_pre_accept_capsule_bounded_buffer` (issue 0157)
  - `prop_h2_capsule_buffer_bounded` (issue 0158)
  - `prop_h2_initiator_parity_validated` (issue 0159)
  - `prop_quic_stream_roundtrip_with_loss` (issue 0089 の再検証)
  - `prop_quic_config_within_varint_never_aborts` (issue 0146 の回帰ピン)
  - `prop_h3_error_code_remap_roundtrip_wire_to_app` (issue 0181 の roundtrip)
- 既存の 12 の `prop_*` ファイルは残しつつ、新規に `prop_*_stateful.py` として追加する (setter 往復の削除は別 issue 予定)
- 各 property test の shrink 効率を上げるため、bytes 生成には `hypothesis.strategies.binary(max_size=4096)` 程度で上限を設ける

## 完了条件

- `tests/` に `RuleBasedStateMachine` ベースのステートフル PBT が h3 / h2 / quic の 3 モジュールに存在すること
- 既知バグ (0145 / 0146 / 0156 / 0157 / 0158 / 0159 / 0089 / 0181) の回帰を検出できること
- ステートフル PBT の実行時間が CI の許容範囲 (数分以内) に収まること
- 既存のテスト全 822 件が引き続き通過すること
