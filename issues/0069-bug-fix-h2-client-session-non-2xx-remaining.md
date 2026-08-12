# HTTP/2 でクライアントが非 2xx 応答を受信してもセッション ID が削除されない

- Created: 2026-08-12
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-client-session-non-2xx-remaining
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/2 のクライアントがサーバーの拒否 (非 2xx 応答) を受信しても `wt_sessions_` のエントリが残り続け、拒否されたセッション ID 宛の `send_datagram` / `send_stream_data` がカプセルをワイヤへ送出し続ける問題を修正する。h3 側の同種問題 (closed issue 0061: `end_headers_cb` のクライアント側分岐で非 2xx 応答受信時に `session_ids_` から削除) の h2 版であり、0063 の実装時にスコープ外として切り出した。

## 現状

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` のレスポンス処理分岐 (`NGHTTP2_HCAT_RESPONSE`) は `:status` が 200 のときのみ `is_established = true` にするが、非 2xx 応答を受信したときに `wt_sessions_` のエントリを削除する処理が存在しない
- エントリの削除は `on_stream_close_callback` (ストリームの両ハーフクローズ時) のみだが、クライアントは END_STREAM を送らないため発火しない。結果として、非 2xx 拒否後もエントリが残り、`is_terminated` も `is_established` も false のまま `send_datagram` のガード (0063 で実装済みのエントリ存在 + 終了フラグ確認) をすり抜けてカプセルがワイヤへ送出される (Sans-IO 構成で実測確認済み)
- サーバー側の `reject_session` は自側のエントリを `wt_sessions_.erase` する (クライアント側のエントリは削除されない)

## 設計方針

- `on_frame_recv_callback` のレスポンス処理分岐で、受信した `:status` が 2xx 以外の場合に `wt_sessions_` からエントリを削除するか、`is_terminated = true` を立てる (0061 の h3 側は「黙って削除」で確定した。h2 側も SessionClosed イベントは発火しない方針を想定)
- 削除条件は「200 以外」ではなく「2xx 以外」とする: h2 の仕様は 2xx 全般をセッション確立として扱う (draft-ietf-webtrans-http2-15 Section 3.2) ため、200 以外で削除すると有効なセッションを誤って削除する
- 変更対象は `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (レスポンス処理分岐)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- サーバーが非 2xx (例: 403) で拒否した場合、クライアントの `wt_sessions_` からセッション ID が削除される (または終了フラグが立つ)
- 拒否されたセッション ID 宛の `send_datagram` がカプセルをワイヤへ送出しない
- 拒否されたセッションに対して SessionClosed イベントが発火しない (黙って削除)
- 通常のセッション確立 (200 応答) と 2xx 非 200 応答 (201 等) のセッションは誤って削除されない
- モックなしの Sans-IO テストで検証できる (0063 で新設した h2 用 Sans-IO ヘルパーを再利用する)
