# WebTransport over HTTP/2 で不正な状態のストリームへの WT_STREAM_DATA_BLOCKED 受信を検知しない

- Created: 2026-09-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-stream-data-blocked-state-error
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 6.9 の MUST「A stream error (Section 3.4) of type WT_STREAM_STATE_ERROR MUST be sent if a WT_STREAM_DATA_BLOCKED capsule is received for a stream that is not in a valid state」が未実装である。

`src/bindings/webtransport_h2.cpp` の `H2Session::process_capsule` は WT_STREAM_DATA_BLOCKED を何もせず読み捨てるため、非コンプライアントなピアの不正カプセルを検知できない。0084 が同種のストリーム状態検証 MUST (Section 6.2 / 6.3 / 6.4) をまとめて対応した際に Section 6.9 をスコープ外とし、その後も起票されていない。

## 現状

- `H2Session::process_capsule` のカプセル種別 switch で `CapsuleType::WtStreamDataBlocked` は `CapsuleType::Padding` / `CapsuleType::WtDataBlocked` と同じ「状態更新のみ不要」の分岐に落ちる。専用のハンドラが存在しない
- そのためペイロード (Stream ID と Maximum Stream Data) を解釈せず、方向検証もストリーム状態の検証も行わない。イベント通知も無い
- 送信側は実装済みである。`CapsuleType::WtStreamDataBlocked` を送出するのは `H2Session::send_stream_data` の保留経路のみで、同関数の冒頭の送信状態ガード (FIN 送出後の `StreamState::DataSent` と `H2Session::reset_stream` 後の `StreamState::ResetSent` を塞ぐ) により Section 6.9 の MUST NOT (閉じた・リセット済みのストリームへ送らない) を満たしている。受信側 MUST だけが欠けている
- draft-15 の BLOCKED 系 3 種のうち、受信側にストリーム状態の MUST を持つのは Section 6.9 だけである。Section 6.8 の WT_DATA_BLOCKED には無く、Section 6.10 の WT_STREAMS_BLOCKED の MUST (Maximum Streams が 2^60 超なら WT_FLOW_CONTROL_ERROR) は `H2Session::handle_wt_streams_blocked` に実装済み
- `tests/` に WT_STREAM_DATA_BLOCKED をワイヤ注入するテストが無い

## 設計方針

- 他のフロー制御カプセルのハンドラ (`H2Session::handle_wt_max_stream_data` / `H2Session::handle_wt_stop_sending`) と同じ構成の受信ハンドラを追加する。完全デコード → `is_receivable_flow_capsule` による方向検証 → ストリーム状態の検証 → 必要なら `H2Session::report_stream_state_error` の順に揃える
- 「valid でない状態」は受信側の終端 (`WtStreamInfo::recv_state` が `StreamState::DataRecvd` または `StreamState::ResetRecvd`) とする。WT_STREAM_DATA_BLOCKED はピアの送信方向 (自側の受信方向) のクレジットに関する申告であり、Section 6.9 の MUST NOT が禁じる「閉じた・リセット済みのストリームへの送出」はこの状態に対応する。判定は `H2Session::handle_wt_stream` / `H2Session::handle_wt_reset_stream` の終端検出と同じ条件にする
- 送信側の終端 (`send_state`) は判定に含めない。自側の FIN は自側の送信方向だけを閉じるため、ピアの送信方向は継続する (Section 5.2 の QUIC 状態ミラー)
- 有効な状態への受信は受理し、状態更新は行わない。ピアの申告であり自側のフロー制御状態を変えない
- `CapsuleType::WtStreamDataBlocked` を switch の no-op 分岐から専用ハンドラへ移す

## 完了条件

- 受信側が終端したストリームへ WT_STREAM_DATA_BLOCKED を受信したとき、WT_STREAM_STATE_ERROR (0x51) が送出される
- エラーを検知した側に Error イベント (0x51) が通知される
- 有効な状態のストリーム (および暗黙作成される未使用の Stream ID) へ受信したときはエラーにならず、セッションが維持される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `H2Session::handle_wt_stream_data_blocked` を追加し、Stream ID と Maximum Stream Data を `decode_varint` で読む。デコード失敗時は他のハンドラと同じく何もせず return する
- `is_receivable_flow_capsule` で方向検証し、違反時は `H2Session::report_stream_state_error` を呼ぶ (メッセージは `"WT_STREAM_DATA_BLOCKED received for local receive-only stream"`)
- `H2Session::streams` のエントリが存在し `recv_state` が終端なら、`H2Session::report_stream_state_error` を `"WT_STREAM_DATA_BLOCKED received for stream in terminal state"` で呼ぶ。エントリが無い場合は暗黙作成せず受理する (`H2Session::handle_wt_max_stream_data` が未作成ストリームを暗黙作成しないのと同じ扱い)
- `tests/test_webtransport_h2_stream_state_error.py` に、FIN 受信済みのストリームと WT_RESET_STREAM 受信済みのストリームへの注入で WT_CLOSE_SESSION (0x51) が送出されること、および Error イベント (0x51) が届くことを検証するテストを追加する
- `tests/test_webtransport_h2_flow_control_capsule.py` に、正常なストリームと未作成の Stream ID への注入でエラーが出ずセッションが維持されることを検証するテストを追加する
