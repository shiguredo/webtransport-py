# 接続終了時に session_ids_ がクリアされない

- Created: 2026-08-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-session-ids-connection-close-cleanup
- Polished: {YYYY-MM-DD}

## 目的

接続終了 (nghttp3 の shutdown) 時に `session_ids_` がクリアされず、接続終了後の `send_datagram` がメンバーシップ確認を通過してデータグラムを送出し得る問題を修正する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `shutdown_cb` は `closed_ = true` を設定するだけで `session_ids_` をクリアしない
- 接続終了後も `send_datagram` のメンバーシップ確認 (`session_ids_.count`) を通過し、`pending_datagrams_` にデータグラムが積まれる
- 実害は QUIC 層 (接続終了後は送出されない) のため限定的だが、セッション終了の MUST (draft-ietf-webtrans-http3-16 Section 6) の「終了後の送信禁止」を満たさない経路が残る
- セッション終了の 3 経路 (`close_stream` による CONNECT ストリームのクローズ / `close_session` / `recv_wt_close_session_cb`) はすべて `session_ids_` から削除するが、接続終了は 3 経路に含まれない

## 設計方針

- `shutdown_cb` で `session_ids_` をクリアする
- セッションごとに SessionClosed イベントを発火するか、クリアのみにするかは調査対象とする (接続終了時の高レベル API の挙動との整合に注意)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h`、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- 接続終了後に `send_datagram` を呼んでも `get_datagrams_to_send` に現れない
- 既存のセッション終了検知 (CONNECT ストリームのクローズ / WT_CLOSE_SESSION) は影響を受けない
- モックなしのテストで検証できる
