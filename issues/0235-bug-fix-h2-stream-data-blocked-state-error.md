# WebTransport over HTTP/2 で不正な状態のストリームへの WT_STREAM_DATA_BLOCKED 受信を検知しない

- Created: 2026-09-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-stream-data-blocked-state-error
- Polished: 2026-09-18

## 目的

draft-ietf-webtrans-http2-15 Section 6.9 の MUST「A stream error (Section 3.4) of type WT_STREAM_STATE_ERROR MUST be sent if a WT_STREAM_DATA_BLOCKED capsule is received for a stream that is not in a valid state」が未実装である。

`src/bindings/webtransport_h2.cpp` の `H2Session::process_capsule` は WT_STREAM_DATA_BLOCKED を何もせず読み捨てるため、非コンプライアントなピアの不正カプセルを検知できない。0084 が Section 6.2 / 6.4 のストリーム状態検証 MUST をまとめて対応した際に Section 6.9 をスコープ外とし (Section 6.3 は 0098、Section 6.6 は 0210 が対応した)、Section 6.9 はその後も起票されていない。

## 現状

- `H2Session::process_capsule` のカプセル種別 switch で `CapsuleType::WtStreamDataBlocked` は `CapsuleType::Padding` / `CapsuleType::WtDataBlocked` と同じ「状態更新のみ不要」の分岐に落ちる。専用のハンドラが存在しない
- そのためペイロード (Stream ID と Maximum Stream Data) を解釈せず、方向検証もストリーム状態の検証も行わない。イベント通知も無い
- WT_STREAM_DATA_BLOCKED はデータ送信者が「送りたいがストリーム単位のフロー制御で塞がれている」ことを伝えるカプセルであり、流れる向きは WT_STREAM / WT_RESET_STREAM と同じ送信者→受信者である (Section 6.9 の "A sender SHOULD send a WT_STREAM_DATA_BLOCKED capsule ... when it wishes to send data but is unable to do so due to stream-level flow control")。したがって受信側の方向検証は `is_receivable_data_capsule` の群に入る
- 送信側は実装済みである。`CapsuleType::WtStreamDataBlocked` を送出するのは `H2Session::send_stream_data` の保留経路のみで、同関数の冒頭の送信状態ガード (FIN 送出後の `StreamState::DataSent` と `H2Session::reset_stream` 後の `StreamState::ResetSent` を塞ぐ) により Section 6.9 の MUST NOT (閉じた・リセット済みのストリームへ送らない) を満たしている。受信側 MUST だけが欠けている
- draft-15 の BLOCKED 系 3 種のうち、受信側にストリーム状態の MUST を持つのは Section 6.9 だけである。Section 6.8 の WT_DATA_BLOCKED には無く、Section 6.10 の WT_STREAMS_BLOCKED の MUST (Maximum Streams が 2^60 超なら WT_FLOW_CONTROL_ERROR) は `H2Session::handle_wt_streams_blocked` に実装済み
- 受信側の状態検証を確かめるテストが無い。`tests/test_webtransport_h2_flow_control_replenishment.py` と `tests/test_webtransport_h2_close_session.py` は送信側が積んだ WT_STREAM_DATA_BLOCKED を `H2Session::receive` に通しているが、いずれもフロー制御の再開が目的で、受理してセッションが維持されることを暗黙に固定しているだけである

## 設計方針

- 他のフロー制御カプセルのハンドラ (`H2Session::handle_wt_max_stream_data` / `H2Session::handle_wt_stop_sending`) と同じ構成の受信ハンドラを追加する。完全デコード → 方向検証 → ストリーム状態の検証 → 必要なら `H2Session::report_stream_state_error` の順に揃える。方向検証は、フロー制御群が使う `is_receivable_flow_capsule` (受信者→送信者方向) ではなく、データ群と同じ `is_receivable_data_capsule` (送信者→受信者方向) を使う
- 「valid でない状態」は受信側の終端 (`WtStreamInfo::recv_state` が `StreamState::DataRecvd` または `StreamState::ResetRecvd`) とする。WT_STREAM_DATA_BLOCKED はピアの送信方向 (自側の受信方向) の申告であり、Section 6.9 の MUST NOT が禁じる「閉じた・リセット済みのストリームへの送出」はこの状態に対応する。判定は `H2Session::handle_wt_reset_stream` の終端検出と同じく `recv_state` の終端を見る (`H2Session::handle_wt_stream` はデータ長 0 のカプセルを許すため `data_len > 0` を伴うが、WT_STREAM_DATA_BLOCKED にはその例外が無い)
- 送信側の終端 (`send_state`) は判定に含めない。自側の FIN は自側の送信方向だけを閉じるため、ピアの送信方向は継続する (Section 5.2 の QUIC 状態ミラー)
- 有効な状態への受信は受理し、状態更新は行わない。ピアの申告であり自側のフロー制御状態を変えない (Section 6.10 の `H2Session::handle_wt_streams_blocked` が「advisory な通知のため状態は更新しない」としているのと同じ扱い)
- エントリが無い Stream ID は、まだ開かれていない未使用の ID と、`H2Session::maybe_release_stream` が解放した ID の 2 種類がある。前者は受理し、後者は Section 6.9 の MUST の対象である。解放済みかどうかを判定する記録は 0236 が導入するため、本 issue では未使用 ID の受理までを実装し、0236 の実装時に同じ記録を本カプセルでも参照するよう揃える (0234 の設計方針と同じ実装順序の申し送り)
- `CapsuleType::WtStreamDataBlocked` を switch の no-op 分岐から専用ハンドラへ移す

## 完了条件

- 受信側が終端した (`recv_state` が `StreamState::DataRecvd` または `StreamState::ResetRecvd`) ストリームへ WT_STREAM_DATA_BLOCKED を受信したとき、WT_STREAM_STATE_ERROR (0x51) が送出され、Error イベント (0x51) が通知される
- 自側送信専用 (自側 initiator + 単方向) の Stream ID へ受信したとき、WT_STREAM_STATE_ERROR (0x51) が送出される
- 有効な状態のストリーム (`H2Session::streams` にエントリがあり `recv_state` が終端でないストリーム) へ受信したときは、エラーにならずセッションが維持される
- これまでに開かれていない未使用の Stream ID へ受信したときも、エラーにならずセッションが維持される (ストリームエントリは作成しない)
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `H2Session::handle_wt_stream_data_blocked` を追加し、Stream ID と Maximum Stream Data を `decode_varint` で読む。デコード失敗時は他のハンドラと同じく何もせず return する
- `is_receivable_data_capsule` で方向検証し、違反時は `H2Session::report_stream_state_error` を `"WT_STREAM_DATA_BLOCKED received for local send-only stream"` で呼ぶ (データ群の `"WT_STREAM received for local send-only stream"` / `"WT_RESET_STREAM received for local send-only stream"` と同じ形)
- `H2Session::streams` のエントリが存在し `recv_state` が終端なら、`H2Session::report_stream_state_error` を `"WT_STREAM_DATA_BLOCKED received for terminal stream"` で呼ぶ。メッセージは 4 バイトの Application Error Code と合わせて 64 バイト未満に収める (`tests/test_webtransport_h2_stream_state_error.py` と `tests/test_webtransport_h2_flow_control_capsule.py` の `_encode_wt_close_session_capsule` が Length を 1 バイト varint で組み立てるため)。収まらない長さにする場合は `tests/test_webtransport_h2_close_session.py` の `_encode_wt_close_session_capsule_bytes` と同じ多バイト varint 対応のヘルパを用意する
- エントリが無い場合は暗黙作成せず受理する (`H2Session::handle_wt_max_stream_data` が未作成ストリームを暗黙作成しないのと同じ扱い)。解放済み ID の検出は 0236 が導入する記録に委ね、0236 の実装時に本ハンドラも同じ記録を参照する
- `tests/test_webtransport_h2_stream_state_error.py` に、FIN 受信済みのストリームと WT_RESET_STREAM 受信済みのストリームへの注入で WT_CLOSE_SESSION (0x51) が送出されること、および Error イベント (0x51) が届くことを検証するテストを追加する。メッセージ定数は既存の `_WT_*` 群と同じ形で置く
- `tests/test_webtransport_h2_initiator_validation.py` に、自側送信専用の Stream ID への注入で拒否されることと、ピア起点単方向の Stream ID への注入で受理されることを検証するテストを追加する (既存の `test_data_to_send_only_stream_rejected` / `test_stop_sending_to_receive_only_rejected` と同じ形)
- `tests/test_webtransport_h2_flow_control_capsule.py` に、`recv_state` が終端でないストリームと未使用の Stream ID への注入でエラーが出ずセッションが維持されることを検証するテストを追加する。注入先はクライアント起点双方向の ID 0 のように `is_receivable_data_capsule` を通る ID を選ぶ。同ファイルの module docstring が対象節を列挙しているため Section 6.9 を追記する
