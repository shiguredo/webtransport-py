# WebTransport over HTTP/2 で解放済みストリーム ID への WT_STREAM / WT_RESET_STREAM が再作成され終端検出を免れる

- Created: 2026-09-18
- Completed: 2026-09-18
- Branch: feature/fix-h2-released-stream-implicit-create
- Polished: 2026-09-18

## 目的

draft-ietf-webtrans-http2-15 Section 6.4 は「A WT_STREAM capsule MUST NOT be sent after a stream is closed or reset」とし、閉じたストリームへの WT_STREAM 受信に WT_STREAM_STATE_ERROR を送る MUST を定めている。Section 6.2 も WT_RESET_STREAM について同じ MUST NOT / MUST を定めている。

`src/bindings/webtransport_h2.cpp` の `H2Session::maybe_release_stream` がストリームエントリを解放したあと、ピアが同じ Stream ID へ WT_STREAM または Reliable Size 0 の WT_RESET_STREAM を送ると、`H2Session::handle_wt_stream` と `H2Session::handle_wt_reset_stream` はエントリが無いものとして再作成する。終端済みストリームが状態検証をすり抜けて復活し、データやイベントが再配送されるため両 Section の MUST を満たさない。0210 はこの経路をスコープ外とし、WT_MAX_STREAM_DATA の抑止記録をセッション単位に置くことで Section 6.6 の MUST NOT だけを満たした。

あわせて、再作成された `WtStreamInfo` の `max_stream_data_remote` は `H2SessionConfig::wt_initial_max_stream_data` に戻る。ピアが解放前に広告された値とその残量を保持したまま同じ Stream ID へ 1 カプセルで初期値を超えるデータを送ると、`H2Session::handle_wt_stream` の受信超過検査で WT_FLOW_CONTROL_ERROR を出してセッションを閉じる。再作成自体を検出して拒否すればこの経路も塞がる。

## 現状

- `H2Session::handle_wt_stream` は `WtSessionInfo::streams` にエントリが無い場合、方向検証と最大ストリーム数検査を通れば `WtStreamInfo` を新規作成する。解放済みかどうかを示す記録は `H2Session` にも `WtSessionInfo` にも無い
- `H2Session::handle_wt_reset_stream` も同じで、未知ストリームへの Reliable Size 0 の WT_RESET_STREAM はエントリを作成し、受信側を `StreamState::ResetRecvd` へ遷移させて `StreamReset` イベントを push する。解放済みかどうかは見ない
- `H2Session::maybe_release_stream` は双方向は両ハーフ終端、単方向は使う方向の終端で `wt_session->streams.erase` する。エントリを削除する箇所はこの 1 箇所だけである (`H2Session::reset_stream` は `send_state` を `StreamState::ResetSent` に更新するだけで erase せず、解放は `H2Session::maybe_release_stream` が行う)
- `H2Session::handle_wt_stream` の終端検出は `data_len > 0` を条件にしている。データを含まない WT_STREAM は「ストリームを閉じる」操作として許容され、実ブラウザ (WebKit) が FIN 送信後に空の WT_STREAM_FIN を送るため相互運用性の観点から無視する扱いであり、`tests/test_webtransport_h2_stream_state_error.py` の `test_empty_wt_stream_fin_after_fin_ignored` が固定している
- `WtSessionInfo::sent_stop_sending_stream_ids` の宣言コメントは「解放後にピアが同じ Stream ID へ WT_STREAM を送ると `H2Session::handle_wt_stream` が暗黙作成する」ことを、記録をセッション単位に置く理由として明記している。同じ前提は `H2Session::maybe_send_max_stream_data` のコメントと `tests/test_webtransport_h2_stream_state_error.py` の `test_wt_max_stream_data_after_stop_sending_released_stream_sends_state_error` の docstring にも書かれている
- `tests/test_webtransport_h2_flow_control_replenishment.py` の解放後の抑止テストは、解放後に同じ Stream ID へ WT_STREAM を注入すると `get_stream_ids` に再び現れデータが配送される、という現状挙動を前提として固定している
- 0193 は受信系コンテナ (`received_max_stream_data_by_id` / `received_stop_sending_stream_ids`) に固定上限 `kMaxReceivedMapEntries` (4096) の安全弁を設けた。当初の設計方針だった累積ストリーム予算への連動は、ピアのストリーム churn で予算が無制限に水増しされることがレビューで判明したため固定上限へ変更している
- 0234 は `H2Session::stop_sending` の送出済み判定を、0235 は `H2Session::handle_wt_stream_data_blocked` を扱う。0235 は解放済み ID の検出を本 issue が導入する記録に委ねると明記しており、0234 も本 issue との実装順序に言及している
- 本経路に対応する issue は起票されていない

## 設計方針

- `WtSessionInfo` に解放済み Stream ID の集合を追加し、その ID への WT_STREAM / WT_RESET_STREAM の受信を `H2Session::report_stream_state_error` で WT_STREAM_STATE_ERROR として検出する。Section 6.4 と Section 6.2 の MUST を満たす
- 記録は `H2Session::maybe_release_stream` の解放箇所でのみ行う。エントリの削除がこの 1 箇所に集約されているため、`H2Session::reset_stream` 起点の解放も含めて全経路を覆える
- 記録の対象は方向検証で先に拒否されない ID に限る。自側送信専用 (自側 initiator + 単方向) の解放は `is_receivable_data_capsule` で先に拒否されるため記録せず、自側の通常利用 (アプリが単方向ストリームを多数開閉する) で集合が埋まるのを避ける
- 集合の上限は 0193 と同じ **固定上限** `kMaxReceivedMapEntries` とし、超過時は挿入せずセッションを閉じずに検出を諦める。累積ストリーム予算への連動は、解放のたびに `max_streams_*_remote` が +1 されて予算も同じ速度で増えるため上界として機能せず、0193 が同じ理由で撤回している
- データを含まない WT_STREAM (FIN のみ) は解放済み ID でも無視する。既存の終端検出と同じく `data_len > 0` を条件とし、WebKit 相互運用の緩和を維持する。ただしエントリは作成せずに return する (再作成を避ける)
- `H2Session::handle_wt_reset_stream` の未知ストリーム経路も同じ集合を参照する。Reliable Size 0 の受理は集合に無い未使用 ID に限る
- `WtSessionInfo::sent_stop_sending_stream_ids` はセッション単位のまま維持する。解放済み ID の検出を上限で諦めた場合は `H2Session::handle_wt_stream` の再作成経路が残るため、0210 が記録をセッション単位に置いた理由は消えない。0210 の宣言コメントは「解放済み ID の検出を上限で放棄した場合は再作成が起こり得るため、セッション単位の記録が必要」に書き換える
- 完了条件は集合に記録されている範囲で成立する。上限超過後に検出が失われることは 0193 と同じ既知の制約としてコメントに残す

## 完了条件

- 解放済み集合に記録されている Stream ID への、データを含む WT_STREAM の受信で WT_CLOSE_SESSION (Type 0x2843, Application Error Code 0x51) が送出され、ストリームが再作成されない
- 解放済み集合に記録されている Stream ID への、Reliable Size 0 の WT_RESET_STREAM の受信でも同じエラーが送出され、ストリームが再作成されない
- 解放済み集合に記録されている Stream ID への、データを含まない WT_STREAM (FIN のみ) はエラーにならず、ストリームも再作成されない
- 集合に記録されていない未使用の Stream ID への WT_STREAM 受信による暗黙作成が従来どおり動作する
- 上限超過で挿入されなかった解放済み ID では検出されない (既知の制約)。その場合も 0210 の WT_MAX_STREAM_DATA の抑止が維持される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h2.h` の `WtSessionInfo` に `released_stream_ids` を追加し、`H2Session::maybe_release_stream` の `wt_session->streams.erase` の直前で挿入するようにした。自側送信専用 (自側 initiator + 単方向) は方向検証で先に拒否されるため記録せず、上限は受信系コンテナと同じ固定上限 `kMaxReceivedMapEntries` とした (上限超過後は再作成が起こり得るという既知の制約)
- `H2Session::handle_wt_stream` のエントリ不在時に解放済み判定を追加し、最大ストリーム数検査より前に置いた。データを含む WT_STREAM は `"WT_STREAM received for released stream"` で WT_STREAM_STATE_ERROR にし、データを含まない WT_STREAM は終端状態への受信と同じく無視してエントリも作成しない (Section 6.4 の空カプセルの扱い)
- `H2Session::handle_wt_reset_stream` のエントリ不在時に解放済み判定を追加し、Reliable Size の検証より前に置いた。`"WT_RESET_STREAM received for released stream"` で WT_STREAM_STATE_ERROR にする
- 0235 から申し送られた `H2Session::handle_wt_stream_data_blocked` にも同じ判定を追加し、`"WT_STREAM_DATA_BLOCKED received for released stream"` で WT_STREAM_STATE_ERROR にする (Section 6.9)
- `kMaxReceivedMapEntries` のコメントに `released_stream_ids` と、上限超過で対象外になる 5 つの検出を追記した。`WtSessionInfo::sent_stop_sending_stream_ids` の宣言コメントは、解放済み ID の検出を上限超過で諦めた場合は `H2Session::handle_wt_stream` の再作成経路が残るためセッション単位の記録が必要である旨に書き換えた (`H2Session::maybe_send_max_stream_data` のコメントは宣言コメントを参照しているだけなので変更なし)
- `tests/test_webtransport_h2_stream_state_error.py` に 5 件追加した。`test_wt_stream_after_release_sends_state_error` (解放済み ID へのデータ付き WT_STREAM)、`test_wt_reset_stream_after_release_sends_state_error` (Reliable Size 0 の WT_RESET_STREAM)、`test_wt_reset_stream_after_release_prefers_released_over_reliable_size` (Reliable Size 0 以外でも解放済みとして報告されることの順序ピン)、`test_wt_stream_fin_after_release_ignored` (データを含まない WT_STREAM はエラーもイベントもエントリ作成も起きない)、`test_wt_stream_data_blocked_after_release_sends_state_error`。共通の解放手順は `_release_peer_uni_stream`、Error イベントの表明は `_assert_error_event` に集約し、module docstring に Section 6.4 / 6.2 / 6.9 の解放済み検出を追記した
- `tests/test_webtransport_h2_flow_control_replenishment.py` の `test_max_stream_data_not_sent_after_stop_sending_released_stream` を `test_max_stream_data_not_sent_after_released_map_full` に改名し、契約を「解放済み ID の記録が固定上限に達した後も Section 6.6 の抑止が維持される」に変更した (解放前の停止状態での抑止は `test_max_stream_data_not_sent_after_stop_sending` が引き続き担う)。充填数は `_RELEASED_MAP_LIMIT` として定数化し、抑止の確認前にキュー済みカプセルをピアへ掃き出して送信ウィンドウの残量で表明が空振りしないようにした。module docstring にも上限到達時の抑止を追記した
- `tests/test_webtransport_h2_stream_state_error.py` の `test_wt_max_stream_data_after_stop_sending_released_stream_sends_state_error` の docstring を、上限超過時に再作成が起こり得るためセッション単位の記録が必要である旨に更新した
- `CHANGES.md` の `## develop` にはエントリを追加していない。`CODEBASE.md` に「この指示がなくなるまでは変更履歴を `CHANGES.md` に残さないこと」という指示がある
- 完了条件には Section 6.9 (WT_STREAM_DATA_BLOCKED) の項目を挙げていなかったが、0235 からの申し送りにより実装とテストに含めている
- 全テスト (1198 本) が通ることを確認した
