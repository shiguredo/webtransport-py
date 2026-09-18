# WebTransport over HTTP/2 で不正な状態のストリームへの WT_STREAM_DATA_BLOCKED 受信を検知しない

- Created: 2026-09-18
- Completed: 2026-09-18
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

- `src/bindings/webtransport_h2.cpp` に `H2Session::handle_wt_stream_data_blocked` を追加した。Stream ID と Maximum Stream Data を `decode_varint` で完全にデコードし (値は受信側の状態を変えないため読み捨てる)、`is_receivable_data_capsule` で方向検証し、`WtStreamInfo::recv_state` が終端 (`StreamState::DataRecvd` / `StreamState::ResetRecvd`) なら `H2Session::report_stream_state_error` を呼ぶ。送信側の終端 (`send_state`) は含めない (自側の FIN は自側の送信方向だけを閉じるため)
- 方向はデータ送信者→受信者であるため、フロー制御群の `is_receivable_flow_capsule` ではなくデータ群と同じ `is_receivable_data_capsule` を使う。違反時のメッセージは `"WT_STREAM_DATA_BLOCKED received for local send-only stream"`、終端時は `"WT_STREAM_DATA_BLOCKED received for terminal stream"`。後者は Application Error Code 4 バイトと合わせて 64 バイト未満に収める必要があるため (テストの WT_CLOSE_SESSION 検証が Length を 1 バイト varint で組み立てる)、既存の `"WT_STREAM received for stream in terminal state"` とは語順を変えている
- エントリが無い場合は暗黙作成せず受理する。解放済み ID は記録が無いため未作成 ID と同じ経路で受理される既知の制約があり、検出は 0236 が導入する記録に委ねる
- `H2Session::process_capsule` の `CapsuleType::WtStreamDataBlocked` を no-op 分岐から専用ハンドラの呼び出しへ移し、`src/bindings/webtransport_h2.h` に宣言を追加した
- `tests/conftest.py` に `_encode_wt_stream_data_blocked_capsule` を追加した (既存の `_encode_wt_max_stream_data_capsule` と同じ形)
- `tests/test_webtransport_h2_stream_state_error.py` に 2 件追加した。`test_wt_stream_data_blocked_for_terminal_stream_sends_state_error` はピアの FIN で受信側が終端したストリームへの注入で、`test_wt_stream_data_blocked_after_reset_sends_state_error` は WT_RESET_STREAM で終端したストリームへの注入で、いずれも WT_CLOSE_SESSION (Type 0x2843, Application Error Code 0x51) の送出と Error イベント (0x51) の通知を検証する
- `tests/test_webtransport_h2_initiator_validation.py` に 2 件追加した。`test_stream_data_blocked_to_send_only_stream_rejected` は自側送信専用の ID 3 への注入が拒否されること (データ群の方向検証を使うことの回帰ピン)、`test_stream_data_blocked_to_peer_uni_accepted` はピア起点単方向の ID 2 への注入が受理されエントリも作成されないことを検証する
- `tests/test_webtransport_h2_flow_control_capsule.py` に 3 件追加した。`test_wt_stream_data_blocked_accepted_for_open_stream` (有効な状態)、`test_wt_stream_data_blocked_accepted_for_unknown_stream` (未使用の Stream ID でエントリ非作成)、`test_wt_stream_data_blocked_accepted_after_local_fin` (自側 FIN 送出後 = send_state 終端 / recv_state 非終端でも受理することの回帰ピン)。同ファイルの module docstring に Section 6.9 を追記した
- `tests/test_webtransport_h2_stream_state_error.py` の module docstring にも WT_STREAM_DATA_BLOCKED (Section 6.9) を追記した
- `CHANGES.md` の `## develop` にはエントリを追加していない。`CODEBASE.md` に「この指示がなくなるまでは変更履歴を `CHANGES.md` に残さないこと」という指示がある
- 全テスト (1193 本) が通ることを確認した
