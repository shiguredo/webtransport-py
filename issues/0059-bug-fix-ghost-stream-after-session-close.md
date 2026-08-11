# close_session / WT_CLOSE_SESSION 受信後に終了したセッション ID 宛のデータストリームが配信される

- Created: 2026-08-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-ghost-stream-after-session-close
- Polished: 2026-08-11

## 目的

draft-ietf-webtrans-http3-16 Section 6 のセッション終了 (WT_CLOSE_SESSION の送出または受信) 後に、終了したセッション ID 宛のデータストリームが `recv_wt_data_cb` 経由でアプリに配信される問題を修正する。セッション終了を学習した後も、そのセッションのデータがアプリへ届き続けるのは仕様の MUST (Section 6「終了を学習したエンドポイントは、属するストリームの受信側の読み取りを中止しなければならない (MUST abort reading on the receive side)」) に反する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `close_session` (WT_CLOSE_SESSION 送出) と `recv_wt_close_session_cb` (WT_CLOSE_SESSION 受信) は、`session_ids_` からセッション ID を削除し `erase_session_streams` でローカル管理を清掃するが、nghttp3 の CONNECT ストリームはストリームテーブルに残したままにする
- nghttp3 の `nghttp3_conn_close_wt_session` は CONNECT ストリームを削除しない (WRITE_END_STREAM を立て既存のデータストリームをシャットダウンするが、CONNECT ストリーム自体はストリームテーブルに残る)。そのため CONNECT ストリームはコネクション終了まで残存する。WT_CLOSE_SESSION 受信経路でも nghttp3 は CONNECT ストリームを IGN_REST にするだけで削除せず、`end_stream` コールバックが呼ばれない (FIN 経路の `close_stream` が機能しない) ため、`recv_wt_data_cb` 側から見た残存は恒久である
- そのため、セッション終了後にピアがそのセッション ID 宛のデータストリームを開いて送信すると、受信経路の `nghttp3_conn_on_wt_stream` が残存する CONNECT ストリームを `nghttp3_conn_find_stream` で発見してストリームを受容し、`recv_wt_data_cb` が `session_ids_` を検証せず StreamData イベントを発火するため、終了済みセッションの STREAM_DATA としてアプリに配信される
- 対照的に CONNECT ストリームのクローズ経路 (`close_stream` による `nghttp3_conn_close_stream`) では CONNECT ストリームが削除されるため、late データストリームは NGHTTP3_ERR_WT_SESSION_GONE で破棄される (この経路は既に正しく動作する)

## 設計方針

- `recv_wt_data_cb` の冒頭で `session_id` の `session_ids_` メンバーシップを確認し、存在しない場合 (`session_ids_.count(session_id) == 0`) は StreamData イベントを発火せずに破棄する。これは closed issue 0056 の調査で判明した問題であり、0056 の設計候補 1 (「WT データストリームの受信時に、ヘッダーから復元したセッション ID が `session_ids_` に存在しない場合にストリームを破棄する」) がそのまま当てはまる。0056 の設計候補 2 (「nghttp3 に『閉じたセッション』を通知する手段」) は、nghttp3 には「セッションを閉じたことを通知する」専用 API が存在しない (`nghttp3_conn_close_wt_session` は WT_CLOSE_SESSION カプセルの送出専用で CONNECT ストリームを削除せず、`nghttp3_conn_close_stream` は 2 つ目の SessionClosed イベント発火と WT_CLOSE_SESSION カプセル送出不能を招く) ため適用しない
- 破棄はアプリ境界での配信抑止であり、StreamData イベントを発火しないことと、`stream_info_` への登録をスキップすることにより行う (コールバックは 0 を返し、受信データは nghttp3 が消費済みとして扱う)。トランスポート側の後始末 (`nghttp3_conn_close_stream` によるクローズ) は行わない。理由は 2 つ: コールバック内では nghttp3 を再呼び出ししないという既存方針 (`pending_fin_session_ids_` と同じ再入回避。`nghttp3_conn_close_stream` は `nghttp3_conn_read_stream2` の処理中に発火するこのコールバック内で呼ぶと、処理中のストリームが解放されて use-after-free になる) と、受信済み ghost ストリームにはローカル送信バッファが無いため未送信データの解放が不要であること。本修正はアプリ配信の抑止のみを目的とし、仕様 MUST (Section 6「受信側の読み取り中止」) が要求するトランスポート側の読み取り中止 (STOP_SENDING / RESET_STREAM 送出) は実装しない (スコープ外)。破棄した ghost ストリームはピアの FIN / RESET まで nghttp3 のストリームテーブルに残存する (悪意ピアが無制限に開くとテーブルに蓄積し得るが、これは本修正のスコープ外の既知の制約として許容する)。破棄した ghost ストリームが後でピアから FIN / RESET された場合は、`stream_info_` に未登録のため `stream_close_cb` / `reset_stream_cb` / `stop_sending_cb` が `session_id = -1` で発火する (既存の未登録ストリームと同じ挙動)。破棄の根拠は draft-ietf-webtrans-http3-16 Section 4 の「closed session 宛のデータの扱いは Section 6 に従う (endpoints handle data for closed sessions as described in Section 6)」に基づき、終了したセッションのデータをアプリへ配信し続けないことである
- 生存セッション (`session_ids_` に存在するセッション ID) のデータストリーム受信は従来どおり配信する
- 送信側の穴 (`close_session` 後の `open_stream` が成功する) は本 issue のスコープ外とする (open issue 0060 が担当)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`recv_wt_data_cb` のセッション ID 検証)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- `close_session` で WT_CLOSE_SESSION を送出した後、そのセッション ID 宛のデータストリームのデータがアプリに配信されない
- WT_CLOSE_SESSION を受信した後も同様に配信されない
- 生存セッションのデータストリーム受信は影響を受けない
- 破棄した ghost ストリームの後続 FIN / RESET で、`stream_close_cb` / `reset_stream_cb` / `stop_sending_cb` が `session_id = -1` で発火する (既存の未登録ストリームと同じ挙動)
- CONNECT ストリームのクローズ経路 (既に正しく動作する経路) の late データストリーム破棄が回帰しない
- モックなしの Sans-IO テストで検証できる (既存の `tests/conftest.py` の Sans-IO 構成 `_establish_session` / `_pump` を流用する)。ghost ストリームの注入は、セッション終了により `session_ids_` から削除された**受信側**の `receive_stream_data` へ、WT データストリームのワイヤ形式 (セッション ID を含むヘッダー) を直接注入する構成で再現する (例: `client` が `close_session` で送出した場合は `server` が受信側。0060 の実装後は `open_stream` が失敗して API 経由では注入できないため、ワイヤ形式直接注入に依存する)
