# h3 低レベル e2e の RESET_STREAM_AT テストが pacing と送信待ちの残留で CI で失敗する

- Created: 2026-10-08
- Completed: 2026-10-08
- Branch: feature/test-fix-stream-reset-at-flakiness
- Reporter: @voluntas

## 目的

CI が `tests/test_e2e_webtransport_h3_low_level.py` の
`test_stream_reset_at_recovers_session_id` で断続的に失敗するのを解消する。
2026-10-03 の wheel ワークフロー (macos-26_arm64 / 3.14t) では
`assert info.reset_session_id == session_id` が `-1 == 0` で落ちている。
ローカルで 30000 回実行すると 7 回失敗した (0.023%)。

## 現状

- 失敗は 2 種類ある
  1. `info.reset_received` の 5 秒待ちが TimeoutError になる。RESET_STREAM_AT の
     パケットがワイヤに出ていない。`_send_quic_only` の `send()` が pacing の期限待ちで
     パケットを返さないまま空振りし、待機に入るためである
  2. `assert info.reset_session_id == session_id` が `-1 == 0` になる。サーバーは
     リセットを受け取ったがセッション ID を復元できない。保留パケットにデータが
     含まれておらず (`data_received` 未設定)、`stream_buffers_` に残ったデータが
     リセットで破棄されるためである。`send()` は `stream_buffers_` をストリーム ID
     昇順で処理するため、データストリームより小さい ID のストリーム
     (CONNECT リクエスト / 制御ストリーム) に残留データがあると、対象データが
     パケットに含まれない
- 計測 (30000 回、失敗 7 回) の内訳は種類 1 が 3 回、種類 2 が 4 回
- 影響: CI が確率的に失敗する。テスト側の駆動漏れであり、ライブラリの挙動
  (RFC 9000 Section 3.5 と draft-ietf-quic-reliable-stream-reset-09 Section 5) は
  仕様どおりである

## 設計方針

- `_LowLevelClient` に pacing の期限待ちを挟んでパケットを取り出す
  `_next_packet_with_pacing` と、それを用いて送出する `_send_packet_with_pacing` を
  追加する。期限待ちは sleep のみで行う (`tests/conftest.py` の `wait_pacing_timeout` と
  同じ考え方。pacing は期限の経過だけで送出可能になる)。PTO などの遠い期限は
  送信待ちではないため待たない
- `_send_quic_only` を pacing 対応にし、リセットパケットが確実にワイヤへ出るようにする
- 保留パケットを生成する前に送信待ちを掃く `_drain_pending` を追加し、
  `send_stream_data_withheld` の先頭で呼ぶ
- 変更対象: `tests/test_e2e_webtransport_h3_low_level.py`

## 完了条件

- `test_stream_reset_at_recovers_session_id` を 30000 回連続実行して失敗しない
- 同じファイルの他のテストと全テストが通過する
- CI が緑に戻る

## 解決方法

- `_LowLevelClient` に `_next_packet_with_pacing` と `_send_packet_with_pacing` を追加した。
  `send()` が None を返したときは、pacing とみなせる近い期限 (10 ms 以下) まで sleep して
  再試行し、満了済みのタイマーは `handle_timeout()` で進める。期限なし、または遠い期限
  (PTO 等) は送信待ちなしとして打ち切る
- `_send_quic_only` を pacing 対応にした。リセットのパケットが確実にワイヤへ出るため、
  `info.reset_received` の待ちがタイムアウトしなくなる (種類 1 の解消)
- `_drain_pending` を追加し、`send_stream_data_withheld` の先頭で呼ぶようにした。
  保留パケットにデータが確実に含まれ、リセットで未送信データが破棄されなくなる
  (種類 2 の解消)
- 修正後の `test_stream_reset_at_recovers_session_id` は 30000 回連続実行で失敗せず
  (修正前は 7 回失敗)、同ファイルの 9 テストと全テスト (1347 件) が通過した
