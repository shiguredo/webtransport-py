# 確立済み Client / Server ペアへの API 呼び出し系列を検証するステートフル PBT (RuleBasedStateMachine) を導入する

- Created: 2026-09-07
- Completed: 2026-09-12
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

## 解決方法

`RuleBasedStateMachine` によるステートフル PBT を h3 / h2 / quic の 3 モジュールに追加した。既存の 12 の `prop_*` ファイルはそのまま残している。

- `tests/prop_h3_stateful.py`: 確立済み h3 ペアへの `open_stream` / `send_stream_data` / `reset_stream` / `send_datagram` / `close_session` と pump の系列を駆動する。invariant は「SessionClosed が side ごとに高々 1 回」「セッション ID に重複が無い」「正当な操作系列で ERROR イベントが積まれない」で、0145 の QPACK ブロック中 DATA pipeline の SIGABRT 回帰ピンとして機能する
- `tests/prop_h2_stateful.py`: 確立済み h2 ペアへの同種の系列。`get_send_credit` の範囲 (0 以上・広告値以下) を invariant に加え、0156 のフロー制御クレジット回帰ピンとする
- `tests/prop_quic_stateful.py`: 確立済み QUIC ペアへの `open_stream` / `send_stream_data` / `reset_stream` / `send_datagram` / `close` と pump の系列。加えて Config の境界値 (`max_data` / `max_streams_bidi` の uint64 全域) で abort せず `ValueError` / `RuntimeError` になることを検証する (0146 の回帰ピン)
- 確立済みペアの作成手順は各ファイル内に持つ (stateful PBT は 1 例ごとに状態を作り直すため)

実装中に判明した点:

- h2 の `Session.open_stream` は stream ID を引数に取らず払い出す (0, 4, 8, ...)。h3 とは API が異なるため、開いた ID を記録して後続 rule が使う形にした
- rule が機械の状態に依存して早期 return すると Hypothesis が `FlakyStrategyDefinition` (data generation の非一貫性) として検出する。`send_datagram` などの rule は無条件に呼び、無効な操作は API 側が黙って無視する形にした
- quic の stateful PBT は pacing 期限を待つ conftest の pump を使うと 1 例あたり十数秒かかったため、待機なしの軽量 pump (最大 10 往復) に置き換えた。3 ファイル合計で 1 秒未満で完走する

`uv run pytest tests/ --timeout=60` の 1114 件が全て通る。
