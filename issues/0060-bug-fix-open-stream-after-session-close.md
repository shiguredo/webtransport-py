# close_session / WT_CLOSE_SESSION 受信後に終了したセッション ID 宛の open_stream が成功する

- Created: 2026-08-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-open-stream-after-session-close
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 6 の MUST「セッション終了を学習したエンドポイントは、新しいデータグラムを送信してはならず、新しいストリームも開いてはならない (it MUST NOT send any new datagrams or open any new streams)」を満たすため、`close_session` (WT_CLOSE_SESSION 送出) と `recv_wt_close_session_cb` (WT_CLOSE_SESSION 受信) で終了したセッション ID 宛の `open_stream` を防ぐ。

## 現状

- `src/bindings/webtransport_h3.cpp` の `open_stream` は `nghttp3_conn_open_wt_data_stream` を呼び、その戻り値で成否を判定する
- `nghttp3_conn_open_wt_data_stream` は、CONNECT ストリームが nghttp3 のストリームテーブルに残存している場合 (`nghttp3_conn_find_stream` が非 NULL) は成功を返す。CONNECT ストリームが削除されるのは `close_stream` による CONNECT ストリームのクローズ経路のみであり、`close_session` (nghttp3 の `nghttp3_conn_close_wt_session` は WRITE_END_STREAM を立てるがストリームを削除しない) と `recv_wt_close_session_cb` では CONNECT ストリームが残存する
- そのため、`close_session` または WT_CLOSE_SESSION 受信後にアプリがそのセッション ID で `open_stream` を呼ぶと、`nghttp3_conn_open_wt_data_stream` が成功して新しいデータストリームが開き、データが送出される。これは Section 6 の MUST 違反をライブラリ自身が誘発し得る状態
- 対照的に CONNECT ストリームのクローズ経路 (`close_stream`) では CONNECT ストリームが削除されるため `NGHTTP3_ERR_INVALID_ARGUMENT` が返って防がれる (この経路は既に正しく動作する)

## 設計方針

- `open_stream` の冒頭で `session_id` の `session_ids_` メンバーシップを確認し、存在しない場合 (`session_ids_.count(session_id) == 0`) は `false` を返して黙って無視する。これは closed issue 0057 の調査で判明した問題であり、0057 の `send_datagram` 修正と同じメンバーシップ確認の方式を `open_stream` にも適用する
- コードコメントに Section 6 の MUST 文面を引用して根拠を明記する
- 生存セッション (`session_ids_` に存在するセッション ID) の `open_stream` は従来どおり成功する
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`open_stream` のメンバーシップ確認)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- `close_session` で WT_CLOSE_SESSION を送出した後、そのセッション ID 宛の `open_stream` が失敗する (データストリームが開かれない)
- WT_CLOSE_SESSION を受信した後も同様に失敗する
- 生存セッションの `open_stream` は従来どおり成功する
- モックなしの Sans-IO テストで検証できる (既存の `tests/test_e2e_webtransport_h3.py` の Sans-IO 構成を流用する)
