# サーバーが reject_session で拒否した後も session_ids_ からセッション ID が削除されない

- Created: 2026-08-12
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-server-session-id-reject-remaining
- Polished: {YYYY-MM-DD}

## 目的

`reject_session` (403 等の非 2xx 応答) で CONNECT リクエストを拒否しても、サーバーの `session_ids_` にセッション ID が残り続け、拒否されたセッション ID 宛の `send_datagram` がデータグラムを送出し得る問題を修正する。クライアント側の同種問題 (issue 0061: 非 2xx 応答受信時の残留) とは独立の経路であり、0061 の磨き上げ時に分割して起票した。

## 現状

- `src/bindings/webtransport_h3.cpp` の `reject_session` は `nghttp3_conn_submit_response` を呼ぶだけで、`session_ids_` からセッション ID を削除しない
- サーバー側の `session_ids_` への挿入は `end_headers_cb` (CONNECT リクエスト受信時) で行われるが、拒否されたセッションは削除経路 (`close_stream` / `close_session` / `recv_wt_close_session_cb`) のいずれにも該当せず、ID が残留する。0057 の `send_datagram` のメンバーシップ確認 (0057 で実装済み) は残留 ID を通過するため、拒否済みセッション ID 宛の `send_datagram` がデータグラムを送出し得る
- 実ネットワークでは、コンフォーマントなクライアント (nghttp3) が 403 受信時に CONNECT ストリームを reset し、サーバー側は `close_stream` 経路で自浄されるため、残留の実害は限定的 (Sans-IO 構成ではリセットが届かず残留が恒久化する)
- 既存テスト `test_pre_accept_fin_not_accepted_keeps_session` (tests/test_webtransport_h3_pre_accept_fin.py) が「受理されない場合 (reject_session 経路) は SessionClosed が発火せず、セッション ID は残留する。現状の挙動を維持する」として残留をピン留めしている

## 設計方針

- `reject_session` で `session_ids_` から削除するか、SessionClosed イベントを発火するかは調査対象とする (0061 のクライアント側は「黙って削除」で確定したが、サーバー側は SessionReady を発火済みのセッションを拒否する点で意味論が異なる。ピン留めテストの書き換えの要否も併せて判断する)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`reject_session`)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- `reject_session` で拒否した後、サーバーの `session_ids_` からセッション ID が削除される
- 拒否されたセッション ID 宛の `send_datagram` がデータグラムを送出しない
- モックなしの Sans-IO テストで検証できる
