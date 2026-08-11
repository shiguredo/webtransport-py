# close_session / WT_CLOSE_SESSION 受信後に終了したセッション ID 宛のデータストリームが配信される

- Created: 2026-08-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-ghost-stream-after-session-close
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 6 のセッション終了 (WT_CLOSE_SESSION の送出または受信) 後に、終了したセッション ID 宛のデータストリームが `recv_wt_data_cb` 経由でアプリに配信される問題を修正する。セッション終了を学習した後も、そのセッションのデータがアプリへ届き続けるのは仕様のセッション終了条件に反する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `close_session` (WT_CLOSE_SESSION 送出) と `recv_wt_close_session_cb` (WT_CLOSE_SESSION 受信) は、`session_ids_` からセッション ID を削除し `erase_session_streams` でローカル管理を清掃するが、nghttp3 の CONNECT ストリームはストリームテーブルに残したままにする
- `nghttp3_conn_close_wt_session` (nghttp3 の `nghttp3_conn_close_wt_session`) は CONNECT ストリームを削除せず WRITE_END_STREAM を立てるだけのため、その後の `nghttp3_conn_open_wt_data_stream` は成功し、`nghttp3_conn_find_stream` が CONNECT ストリームを発見して `recv_wt_data_cb` が呼ばれる
- そのため、セッション終了後にピア (またはコンテキスト喪失後のアプリ自身) がそのセッション ID 宛のデータストリームを開いて送信すると、`recv_wt_data_cb` (`src/bindings/webtransport_h3.cpp` の `recv_wt_data_cb`) が `session_ids_` を検証せず StreamData イベントを発火し、終了済みセッションの STREAM_DATA としてアプリに配信される
- 対照的に CONNECT ストリームのクローズ経路 (`close_stream` による `nghttp3_conn_close_stream`) では CONNECT ストリームが削除されるため、late データストリームは NGHTTP3_ERR_WT_SESSION_GONE で破棄される (この経路は既に正しく動作する)

## 設計方針

- `recv_wt_data_cb` の冒頭で `session_id` の `session_ids_` メンバーシップを確認し、存在しない場合 (`session_ids_.count(session_id) == 0`) は StreamData イベントを発火せずに破棄する。これは closed issue 0056 の調査で判明した問題であり、0056 の設計候補 1 (「WT データストリームの受信時に、ヘッダーから復元したセッション ID が `session_ids_` に存在しない場合にストリームを破棄する」) がそのまま当てはまる
- 破棄の方法は、StreamData イベントを発火しないことと、ストリームを nghttp3 に `nghttp3_conn_close_stream` でクローズして未送信データを解放することの両方を含む (draft-ietf-webtrans-http3-16 Section 6 の MUST: セッション終了時に属するストリームの後始末)。詳細は実装時に nghttp3 の状態機械と整合を確認する
- 生存セッション (`session_ids_` に存在するセッション ID) のデータストリーム受信は従来どおり配信する
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`recv_wt_data_cb` のセッション ID 検証)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- `close_session` で WT_CLOSE_SESSION を送出した後、そのセッション ID 宛のデータストリームのデータがアプリに配信されない
- WT_CLOSE_SESSION を受信した後も同様に配信されない
- 生存セッションのデータストリーム受信は影響を受けない
- モックなしの Sans-IO テストで検証できる (既存の `tests/test_e2e_webtransport_h3.py` の Sans-IO 構成を流用する)
