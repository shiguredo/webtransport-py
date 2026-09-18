# WebTransport over HTTP/2 で解放済みストリーム ID への WT_STREAM が暗黙作成され終端検出を免れる

- Created: 2026-09-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-released-stream-implicit-create
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 6.4 は「A WT_STREAM capsule MUST NOT be sent after a stream is closed or reset」とし、閉じたストリームへの WT_STREAM 受信に WT_STREAM_STATE_ERROR を送る MUST を定めている。

`src/bindings/webtransport_h2.cpp` の `H2Session::maybe_release_stream` が両ハーフ終端した `WtStreamInfo` を `H2Session::streams` から解放したあと、ピアが同じ Stream ID へ WT_STREAM を送ると、`H2Session::handle_wt_stream` はエントリが無いものとして暗黙作成する。終端済みストリームが状態検証をすり抜けて復活し、データが再配送されるため MUST を満たさない。0210 はこの経路をスコープ外とし、WT_MAX_STREAM_DATA の抑止記録をセッション単位に置くことで MUST NOT だけを満たした。

あわせて、再作成された `WtStreamInfo` の `max_stream_data_remote` は `H2SessionConfig::wt_initial_max_stream_data` に戻る一方、ピアは解放前に広告された値を `H2Session::handle_wt_max_stream_data` 経由で保持し続ける。この食い違いにより、ピアが自分に許されていると認識している量を送ると、受信側は `H2Session::handle_wt_stream` の受信超過検査で WT_FLOW_CONTROL_ERROR を出してセッションを閉じる。

## 現状

- `H2Session::handle_wt_stream` は `wt_session->streams` にエントリが無い場合、方向検証と最大ストリーム数検査を通れば `WtStreamInfo` を新規作成する。解放済みかどうかを示す記録は `H2Session` にも `WtSessionInfo` にも無い
- `H2Session::maybe_release_stream` は送信側と受信側の両方が終端した時点で `wt_session->streams.erase` する。単方向ストリームは使う方向の終端で解放される
- `WtSessionInfo::sent_stop_sending_stream_ids` の宣言コメントは「解放後にピアが同じ Stream ID へ WT_STREAM を送ると `H2Session::handle_wt_stream` が暗黙作成する」ことを、記録をセッション単位に置く理由として明記している
- `tests/test_webtransport_h2_flow_control_replenishment.py` の解放後の抑止テストは、解放後に同じ Stream ID へ WT_STREAM を注入すると `get_stream_ids` に再び現れデータが配送される、という現状挙動を前提として固定している
- 本経路に対応する issue は起票されていない

## 設計方針

- 解放済み Stream ID を記録し、その ID への WT_STREAM 受信を `H2Session::report_stream_state_error` で WT_STREAM_STATE_ERROR として検出する。Section 6.4 の MUST を満たす方向であり、0210 がセッション単位で保持している `sent_stop_sending_stream_ids` を `WtStreamInfo` へ戻せる (記録の寿命をストリームに閉じられる)
- 記録はピアが任意に選べる Stream ID を鍵にするため、無制限にするとメモリ DoS になる。0193 が `received_max_stream_data_by_id` / `received_stop_sending_stream_ids` に設けたのと同じ考え方で累積ストリーム予算に連動した上限を設け、超過時はセッションを閉じずに検出を諦める。既知の制約としてコメントに残す
- 記録は `H2Session::maybe_release_stream` の解放箇所でのみ行う。自側 `reset_stream` によるエントリ削除など、解放以外の経路と混同しない
- 0210 が追加した `src/bindings/webtransport_h2.h` の宣言コメントは本挙動を前提にしているため、設計が変わった時点で整合を取る

## 完了条件

- 解放済みストリーム ID への WT_STREAM 受信で WT_STREAM_STATE_ERROR (0x51) が送出され、ストリームが暗黙作成されない
- 未使用の Stream ID への WT_STREAM 受信による暗黙作成が従来どおり動作する
- 解放前の広告値と再作成後の受信許容量が食い違う経路が無くなる (再作成自体が無くなるため)
- 0210 で追加した解放後の抑止テストが新しい契約に追随し、全テストが通過する

## 解決方法

- `WtSessionInfo` に解放済み Stream ID の集合を追加し、`H2Session::maybe_release_stream` の `wt_session->streams.erase` の直前で挿入する
- `H2Session::handle_wt_stream` のエントリ不在時、暗黙作成の前に解放済み集合を確認し、含まれる場合は `H2Session::report_stream_state_error` を `"WT_STREAM received for released stream"` で呼んで return する
- 集合の上限判定は 0193 の `kMaxReceivedMapEntries` と同じ形にし、上限超過時は挿入せず検出を諦める。その場合に Section 6.4 の検出が失われることをコメントに明記する
- `tests/test_webtransport_h2_stream_state_error.py` に、解放済み Stream ID への WT_STREAM 注入で WT_CLOSE_SESSION (0x51) が送出され `get_stream_ids` が変化しないことを検証するテストを追加する
- `tests/test_webtransport_h2_flow_control_replenishment.py` の解放後の抑止テストを新しい契約に合わせて更新する。暗黙作成が起きなくなるため、抑止 (WT_MAX_STREAM_DATA を送らないこと) の検証は解放前の停止状態と組み合わせた形に組み直す
- `WtSessionInfo::sent_stop_sending_stream_ids` を `WtStreamInfo` のフラグへ戻せるかは、解放後の WT_STOP_SENDING 送出を抑止する必要が無くなることで判断する。戻す場合は 0210 の宣言コメントと `H2Session::maybe_send_max_stream_data` のコメントも同時に更新する
