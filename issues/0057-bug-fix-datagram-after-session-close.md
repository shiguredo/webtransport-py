# セッション終了後に send_datagram がデータグラムを送出してしまう

- Created: 2026-08-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-datagram-after-session-close
- Polished: 2026-08-10

## 目的

draft-ietf-webtrans-http3-16 Section 6 の MUST「セッション終了を学習したエンドポイントは、新しいデータグラムを送信してはならない (it MUST NOT send any new datagrams or open any new streams)」を満たすため、セッション終了後の `send_datagram` を無視する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::send_datagram` は Quarter Stream ID を varint でエンコードして `pending_datagrams_` に直接積むだけで、セッション ID の正当性 (`session_ids_` のメンバーシップ) を確認しない
- セッション終了後 (CONNECT ストリームのクローズ後、`close_session` による WT_CLOSE_SESSION 送出後、`recv_wt_close_session_cb` による WT_CLOSE_SESSION 受信後) にアプリがそのセッション ID で `send_datagram` を呼ぶと、データグラムがそのまま送出される。`session_ids_` からの削除は `close_stream` / `close_session` / `recv_wt_close_session_cb` の 3 経路すべてで行われるが、`send_datagram` はそれを参照しない
- 対照的に `open_stream` は、CONNECT ストリームのクローズ経路では `nghttp3_conn_open_wt_data_stream` が終了済みセッションで NGHTTP3_ERR_INVALID_ARGUMENT を返すため防がれる。ただし `close_session` (WT_CLOSE_SESSION 送出) と `recv_wt_close_session_cb` (WT_CLOSE_SESSION 受信) の経路では nghttp3 の CONNECT ストリームが残存するため `open_stream` は成功し得る。本 issue はデータグラム送信の MUST 充足を担当し、`open_stream` 側の穴はスコープ外とする (別 issue 相当)
- 高レベル API の `send_datagram` (`src/webtransport/h3/server.py` の `Server.send_datagram` / `src/webtransport/h3/client.py` の `Client.send_datagram`) は h3 層の `send_datagram` を無検証で呼ぶため、同じ問題に到達する

## 設計方針

- `send_datagram` の冒頭で `session_ids_` のメンバーシップを確認し、終了したセッション ID への送信は黙って無視する (挙動面で `open_stream` の失敗時と同じ「黙って無視」になる。機構は異なる: `open_stream` は nghttp3 のエラー返却に依存し、本対応は `session_ids_` の直接確認)
- コードコメントに Section 6 の MUST 文面を引用して根拠を明記する
- セッション終了の 3 経路 (`close_stream` / `close_session` / `recv_wt_close_session_cb`) すべてが `session_ids_` から削除するため、メンバーシップ確認は MUST の適用範囲を正しくカバーする
- 楽観的送信は維持される: クライアントは `connect` 直後に `session_ids_` へ挿入され (webtransport_h3.cpp の `connect`)、サーバーも CONNECT リクエスト受信時 (`end_headers_cb`) に挿入済みのため、draft-ietf-webtrans-http3-16 Section 4 の「The client MAY optimistically ... send datagrams ... even if it has not yet received the server's response」を妨げない
- メンバーシップ確認は終了したセッション ID に限らず、一度も確立されていないセッション ID への `send_datagram` も無視する挙動変化を含む (低レベル API の意味論の変更。`pending_datagrams_` に積まれず黙って無視される)
- セッション終了**前**に `send_datagram` で `pending_datagrams_` に積まれたデータグラムは、終了後に `get_datagrams_to_send` でそのまま送出される (MUST は「新しいデータグラム」の禁止であり、既にキュー済みの送出は許容される。本 issue のスコープ外)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`send_datagram` のメンバーシップ確認と docstring)、`src/webtransport/h3/server.py` / `src/webtransport/h3/client.py` (docstring に「終了したセッションへの送信は無視される」旨を追記。0049 の前例に合わせる)、テスト (既存の `test_datagram_closed_session_id_still_delivered` の書き換えと、`prop_send_datagram_arbitrary` / `prop_send_datagram_isolated` の意味変化の確認)、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- セッション終了後に `send_datagram` を呼んでもデータグラムが送出されない (`get_datagrams_to_send` に現れない)
- 生存セッションへの `send_datagram` は従来どおり送出される
- セッション終了の 3 経路 (`close_stream` / `close_session` / `recv_wt_close_session_cb`) のすべてで、終了後の `send_datagram` が無視されることを Sans-IO 構成で検証する
- 既存テスト `tests/test_e2e_webtransport_h3.py` の `test_datagram_closed_session_id_still_delivered` は、サーバーの `send_datagram` 経由で閉じたセッション ID 宛てデータグラムが配送されることを検証しており、本対応で送信側が塞がれて落ちる。受信側の検証 (0049 の意図: 閉じたセッション ID のデータグラムをアプリへ届ける) を維持するため、QUIC 層へのワイヤ形式直接注入 (`quic_connection.send_datagram`) に書き換える
- モックなしのテストで検証できる (Sans-IO 構成)
