# close_session / WT_CLOSE_SESSION 受信後に終了したセッション ID 宛の open_stream が成功する

- Created: 2026-08-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-open-stream-after-session-close
- Polished: 2026-08-11

## 目的

draft-ietf-webtrans-http3-16 Section 6 の MUST「セッション終了を学習したエンドポイントは、新しいデータグラムを送信してはならず、新しいストリームも開いてはならない (it MUST NOT send any new datagrams or open any new streams)」を満たすため、`close_session` (WT_CLOSE_SESSION 送出) と `recv_wt_close_session_cb` (WT_CLOSE_SESSION 受信) で終了したセッション ID 宛の `open_stream` を防ぐ。

## 現状

- `src/bindings/webtransport_h3.cpp` の `open_stream` は `nghttp3_conn_open_wt_data_stream` を呼び、その戻り値で成否を判定する
- `nghttp3_conn_open_wt_data_stream` は、CONNECT ストリームが nghttp3 のストリームテーブルに残存している場合 (`nghttp3_conn_find_stream` が非 NULL) は成功を返す。CONNECT ストリームが削除されるのは `close_stream` による CONNECT ストリームのクローズ経路のみであり、`close_session` (nghttp3 の `nghttp3_conn_close_wt_session` は WRITE_END_STREAM を立てるがストリームを削除しない) と `recv_wt_close_session_cb` では CONNECT ストリームが残存する
- そのため、`close_session` または WT_CLOSE_SESSION 受信後にアプリがそのセッション ID で `open_stream` を呼ぶと、`nghttp3_conn_open_wt_data_stream` が成功して新しいデータストリームが開き、データが送出される。これは Section 6 の MUST 違反をライブラリ自身が誘発し得る状態
- 高レベル API の `open_stream` (`src/webtransport/h3/server.py` の `Server.open_stream` / `src/webtransport/h3/client.py` の `Client.open_stream`) は h3 層の `open_stream` を呼ぶ。`Server.open_stream` は h3 層の false を受けて QUIC ストリームをリセットし -1 を返すが、`Client.open_stream` は h3 層の戻り値を無視して stream_id をそのまま返す (修正後は、セッション終了後に呼ばれた場合も stream_id が返り、QUIC ストリームは開くが h3 層への登録が行われずデータは送出されない)

## 設計方針

- `open_stream` の冒頭で `session_id` の `session_ids_` メンバーシップを確認し、存在しない場合 (`session_ids_.count(session_id) == 0`) は `false` を返してストリームを開かない。これは issue 0057 の調査で判明した問題であり、0057 の `send_datagram` 修正と同じメンバーシップ確認の方式を `open_stream` にも適用する。メンバーシップ確認を選ぶ理由は、nghttp3 には「セッションを閉じたことを通知する」専用 API が存在しないためである (`nghttp3_conn_close_wt_session` は CONNECT ストリームを削除しない。0059 の設計候補 2 の検討と同様)
- `open_stream` は bool を返す API であり、`false` は「失敗」を明示的に通知する (0057 の `send_datagram` のような void 返却 API での「黙って無視」とは意味が異なる。高レベル層では `Server.open_stream` が false を受けて -1 を返す)
- コードコメントに Section 6 の該当 MUST 文面 (「it MUST NOT send any new datagrams or open any new streams」を含む文) を引用して根拠を明記する
- 生存セッション (`session_ids_` に存在するセッション ID) の `open_stream` は従来どおり成功する。クライアントは `connect` 直後に `session_ids_` へ挿入済み (webtransport_h3.cpp の `connect`) のため、draft-ietf-webtrans-http3-16 Section 4 の楽観的オープン (クライアントのセッション確立前のストリーム開放) は維持される。サーバー側は CONNECT リクエスト受信時 (`end_headers_cb`) に `session_ids_` へ挿入済みであるが、nghttp3 の `wt.session` は `accept_session` で設定されるため、CONNECT 受信直後 (受理前) の `open_stream` は元々失敗する (本修正の影響を受けない既存の制約)
- 高レベル API のコードは本 issue で変更しない (`Client.open_stream` の戻り値無視は既存の挙動であり、スコープ外。ただし本修正によって高レベル API の観測可能な挙動は変わり得る: `Client.open_stream` は終了セッションで stream_id を返すがデータは送出されず、`Server.open_stream` は -1 を返す)。低レベル `open_stream` の docstring (`src/bindings/webtransport_h3.h`) に「終了したセッション ID では false を返す」旨を追記する
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`open_stream` のメンバーシップ確認と docstring)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- `close_session` で WT_CLOSE_SESSION を送出した後、そのセッション ID 宛の `open_stream` が失敗する (データストリームが開かれない)
- WT_CLOSE_SESSION を受信した後も同様に失敗する
- 生存セッションの `open_stream` は従来どおり成功する (既存の `open_stream` を使う Sans-IO テストが壊れないことの確認を含む)
- 楽観的オープン (クライアントのセッション確立前の `open_stream` が成功する) が維持される (クライアントの `connect` 直後を Sans-IO 構成で検証する)
- CONNECT ストリームのクローズ経路 (`close_stream`) では引き続き `open_stream` が失敗する (既に正しく動作する経路の回帰確認)
- モックなしの Sans-IO テストで検証できる (既存の `tests/conftest.py` の Sans-IO 構成 `_establish_session` / `_pump` を流用する)
