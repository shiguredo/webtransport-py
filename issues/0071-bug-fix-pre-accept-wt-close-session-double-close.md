# 受理前の WT_CLOSE_SESSION が accept_session 中に処理されて SessionClosed が二重発火する

- Created: 2026-08-12
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-pre-accept-wt-close-session-double-close
- Polished: {YYYY-MM-DD}

## 目的

クライアントが受理前 (サーバー応答前) に送った WT_CLOSE_SESSION カプセルが、`accept_session` の処理中にバッファから処理され、SessionClosed イベントが二重発火する・終了済みセッションの CONNECT ストリームに未送信の 2xx が送出される・`session_ids_` にセッション ID が残留する問題を修正する。0065 の実装時にスコープ外として切り出した。

## 現状

- `src/bindings/webtransport_h3.cpp` の `accept_session` は `nghttp3_conn_server_confirm_wt_session` を呼んだ後 (成功時) に、`pending_pre_accept_fin_session_ids_` に含まれるセッションを `pre_accept_fin_accepted_session_ids_` へ移行する
- 受理前に送られた WT_CLOSE_SESSION カプセルは nghttp3 の inq にバッファされ、`nghttp3_conn_server_confirm_wt_session` → `nghttp3_conn_on_wt_session_confirmed` → `nghttp3_conn_process_blocked_wt_stream_data` の経路で **accept_session の処理中に同期処理され、`recv_wt_close_session_cb` が発火する**
- このときの実測挙動 (Sans-IO 構成で確認済み):
  - `recv_wt_close_session_cb` の `session_ids_.erase` は、accept_session 後半の `session_ids_.insert` より前 (confirm の処理中) に実行されるため no-op となり、セッション ID が残留する
  - 0065 の破棄処理の保留記録条件 (`pre_accept_fin_accepted_session_ids_` への挿入が confirm の後) を満たさないため、未送信の 2xx は破棄されず送出される
  - `get_streams_to_send` の遅延クローズループが残留したセッション ID で `close_stream` を実行し、SessionClosed が 2 回目に発火する
  - 残留中は `send_datagram` / `open_stream` が終了を学習済みのセッション ID で成功し得る (draft-ietf-webtrans-http3-16 Section 6 の MUST に反する窓)

## 設計方針

- `accept_session` の移行処理 (`pre_accept_fin_accepted_session_ids_` への挿入) を `nghttp3_conn_server_confirm_wt_session` の前に移動し、recv_wt_close_session_cb の発火時点で移行済みにする (0065 の破棄処理が機能するようになる)
- `recv_wt_close_session_cb` が confirm 中に発火した場合の後始末 (破棄処理の実行タイミング) を確認する: 破棄処理は `receive_stream_data` の read_stream2 後にしか存在しないため、`accept_session` 直後に呼ばれる `get_streams_to_send` が 2xx を先に書き出す経路を塞ぐ必要がある
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`accept_session` の移行順序、破棄処理の実行タイミング)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- 受理前の WT_CLOSE_SESSION が accept_session 中に処理されても、SessionClosed が 1 回だけ発火する
- 終了済みセッションの未送信 2xx が送出されない (0065 の破棄処理が機能する)
- `session_ids_` にセッション ID が残留せず、`send_datagram` / `open_stream` が終了を学習済みのセッション ID で成功しない
- 通常の受理前 FIN の遅延クローズ (accept_session → 2xx 書き出し完了後に close_stream) は影響を受けない
- モックなしの Sans-IO テストで検証できる (クライアントが受理前に WT_CLOSE_SESSION を送出し、サーバーが accept_session した後にイベント・`get_session_ids()`・`get_streams_to_send()` を確認する構成)
