# クライアントが非 200 応答を受信しても session_ids_ からセッション ID が削除されない

- Created: 2026-08-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-client-session-id-non-200-remaining
- Polished: {YYYY-MM-DD}

## 目的

サーバーが CONNECT リクエストを拒否 (403 等の非 200 応答) した場合、クライアントの `session_ids_` にセッション ID が残り続け、拒否されたセッション ID 宛の `send_datagram` がデータグラムを送出してしまう問題を修正する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `connect` は CONNECT リクエスト送信時に `session_ids_` へセッション ID を挿入する
- `end_headers_cb` のクライアント側分岐は `:status` が 200 のときのみ SESSION_READY を発火するが、非 200 応答 (拒否) を受信したときに `session_ids_` から削除する処理が存在しない
- 結果として、サーバーが `reject_session` で 403 を返しても、クライアントの `get_session_ids()` は拒否された ID を返し続け、その ID 宛の `send_datagram` はデータグラムを送出する (Sans-IO 構成で実測確認済み)
- サーバー側の `reject_session` も 403 応答を送るだけで、CONNECT リクエスト受信時に挿入した `session_ids_` の後始末は行わない (サーバー側も同様に残留する)

## 設計方針

- `end_headers_cb` のクライアント側分岐で、非 200 応答を受信した場合に `session_ids_` からセッション ID を削除する
- 削除時に SessionClosed イベントを発火するか、黙って削除するかは調査対象とする (高レベル API の `on_session_closed` の多重発火に注意)
- サーバー側の `reject_session` 後の `session_ids_` 残留も同様に調査・対応を検討する
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h`、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- サーバーが非 200 (例: 403) で拒否した場合、クライアントの `session_ids_` からセッション ID が削除される
- 拒否されたセッション ID 宛の `send_datagram` がデータグラムを送出しない
- 通常のセッション確立 (200 応答) は影響を受けない
- モックなしの Sans-IO テストで検証できる
