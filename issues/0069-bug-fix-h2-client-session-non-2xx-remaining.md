# HTTP/2 でクライアントが非 2xx 応答を受信してもセッション ID が削除されない

- Created: 2026-08-12
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-client-session-non-2xx-remaining
- Polished: 2026-08-12

## 目的

WebTransport over HTTP/2 のクライアントがサーバーの拒否 (非 2xx 応答) を受信しても `wt_sessions_` のエントリが残り続け、拒否されたセッション ID 宛の `send_datagram` がカプセルをワイヤへ送出し続ける問題を修正する。h3 側の同種問題 (closed issue 0061: `end_headers_cb` のクライアント側分岐で非 2xx 応答受信時に `session_ids_` から削除) の h2 版であり、0063 の実装時にスコープ外として切り出した。

## 現状

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` のレスポンス処理分岐 (`NGHTTP2_HCAT_RESPONSE`) は `:status` が 200 のときのみ `is_established = true` にするが、非 2xx 応答を受信したときに `wt_sessions_` のエントリを削除する処理が存在しない
- エントリの削除は `on_stream_close_callback` (ストリームの両ハーフクローズ時) のみだが、クライアントは END_STREAM を送らないため発火しない。結果として、非 2xx 拒否後もエントリが残り、`is_terminated` も `is_established` も false のまま `send_datagram` のガード (0063 で実装済みのエントリ存在 + 終了フラグ確認) をすり抜けてカプセルがワイヤへ送出される (Sans-IO 構成で実測確認済み)
- サーバー側の `reject_session` は自側のエントリを `wt_sessions_.erase` する (クライアント側のエントリは削除されない)

## 設計方針

- `on_frame_recv_callback` のレスポンス処理分岐で、受信した `:status` が 2xx 以外の場合に `wt_sessions_` からエントリを削除する (「黙って削除」。SessionClosed イベントは発火しない)。is_terminated のみを立てる案は不採用とする: `on_stream_close_callback` はエントリが存在する限り SessionClosed を発火するため (拒否後にクライアントが自側を閉じた場合に両ハーフクローズで発火する)、完了条件「SessionClosed が発火しない」を満たせない。エントリ削除なら主要 API (`send_datagram` / `send_stream_data` / `close_session` / `on_stream_close_callback`) がエントリ不在で自然に塞がる (0061 の「削除後は二重発火の経路も残らない」と同じ論理。なお `stop_sending` / `drain_session` は `get_wt_session` を確認せずカプセルを送出するため塞がれないが、本 issue の対象外)
- 削除条件は「200 以外」ではなく「2xx 以外」とする: h2 の仕様は 2xx 全般をセッション確立として扱う (draft-ietf-webtrans-http2-15 Section 3.2 の「A WebTransport session is established when the server sends a 2xx response」) ため、200 以外で削除すると有効なセッションを誤って削除する
- 1xx 中間応答 (100-199) は削除対象外とする: nghttp2 は 1xx も `NGHTTP2_HCAT_RESPONSE` として通知する (最初のレスポンスヘッダーが non-final の 1xx の場合。nghttp2.h の `on_frame_recv_callback` の docstring) が、1xx は中間応答であり拒否ではない。nghttp2 は 1xx で abort せず最終応答を待つため、h3 側 0061 が nghttp3 の挙動 (1xx 受信時に abort) を前提にした「1xx も削除」の論理は h2 には当てはまらない。1xx で削除すると、続く最終応答が `NGHTTP2_HCAT_HEADERS` で届いて既存のレスポンス処理分岐が捕捉しないため、有効なセッションが失われる (1xx 後の最終応答が捕捉されないのは既存の制約として残す。最終応答が 2xx でも非 2xx でも同様で、1xx を挟んだ拒否は本 issue の削除が機能せずエントリが残る)
- 非 2xx 応答はサーバー側 `reject_session` の data provider なし `nghttp2_submit_response` により END_STREAM フラグ付き HEADERS で届く。本 issue のエントリ削除が先に入れば、open issue 0070 (END_STREAM 検知) の終了処理はエントリ不在で無害になる (0070 実装時は、削除されない 201 応答のエントリを END_STREAM 検知が誤って終了処理しないよう、確立済みセッション限定の条件が必要。0070 側で整合を確認する)
- 2xx 非 200 (201 等) のセッションは削除されないが、現状の確立判定 (`:status == "200"` のみ `is_established = true`) のまま `is_established = false` で残る既存の制約が続く。h2 にはクライアント側の FIN 経路による後始末が存在しない (クライアントは END_STREAM を送らない) ため、201 のエントリは後始末経路が存在しない限り残留し続ける (0070 の END_STREAM 検知が確立済みセッション限定なら、201 の残留は 0070 実装後も続く既知の制約として残す)
- エントリ削除後は `close_session` が no-op になるため、拒否されたセッションの HTTP/2 ストリームはサーバー側のみが閉じた半開きのまま接続終了まで残る (RST_STREAM 等の後始末は行わない既知の制約として残す)
- 拒否を学習する前に `http2_stream_buffers_` に積まれたカプセルは、エントリ削除後も flush されると送出され得る (0061 と同じ扱い。禁止対象は「新しいカプセル」であり、既にキュー済みの送出はスコープ外)
- 高レベル API には拒否をアプリへ通知する手段が現状存在しない (イベント化しない)。高レベル `connect()` は SESSION_READY / SESSION_CLOSED を待つタイムアウトなしのループのため、非 2xx 拒否時はイベントが来ず待ち続ける。既知の制約として残す (0061 と同旨。本 issue のスコープ外)
- 変更対象は `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (レスポンス処理分岐と `send_datagram` の docstring / 実装コメントの更新。「クライアントが非 2xx 応答 (拒否) を受けたセッション ID 宛の送信はスコープ外」という 0063 由来の記述を解消する)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ。HTTP/2 であることを文言で明記し、0061 の h3 エントリと区別する)

## 完了条件

- サーバーが非 2xx (例: 403) で拒否した場合、クライアントの `wt_sessions_` からセッション ID が削除される (エントリの削除は公開 API から直接観測できないため、下記の `send_datagram` 送出抑止と SessionClosed 不発火で間接検証する。`get_session_ids()` は `is_established` のエントリのみ返すため検証に使えない)
- 拒否されたセッション ID 宛の `send_datagram` がカプセルをワイヤへ送出しない
- 拒否されたセッションに対して SessionClosed イベントが発火しない (黙って削除)。非 2xx 受信後にクライアントが `close_session` を呼んでも、エントリ不在で no-op になり (WT_CLOSE_SESSION も END_STREAM も送出されない)、SessionClosed が発火しないことを確認する (is_terminated 方式では `close_session` がエントリを残したまま WT_CLOSE_SESSION + END_STREAM を送出し、ピアが閉じた後に `on_stream_close_callback` 経由で SessionClosed が発火するため、両方式の差別化シナリオになる)
- 通常のセッション確立 (200 応答) と 2xx 非 200 応答 (201 等) のセッションは誤って削除されない (201 は既存のリーク挙動のピン留めとして、実際のセッション ID で `reject_session(session_id, 201)` を呼び、クライアントの `send()` が返すワイヤバイト列に DATAGRAM capsule が含まれ続けることを確認する。サーバー側は `reject_session` が自側エントリを削除するため、ピア側の DATAGRAM イベントでは確認できない)
- 1xx 中間応答を受信してもエントリが削除されない (1xx は中間応答であり拒否ではない。h2 の公開 API に 1xx 送出手段が存在しないため、1xx HEADERS フレームのワイヤバイト列 (HPACK 圧縮済みヘッダーブロックを含む) をクライアントの `receive()` に直接注入する構成で検証する。`reject_session` は `nghttp2_submit_response` 経由のため 1xx の送出には使えない。注入後はクライアントの HPACK 動的テーブルが実サーバーのエンコーダーと非対称になるため、注入はテスト内の最後の操作にする等の配慮が必要)
- モックなしの Sans-IO テストで検証できる (0063 で新設した h2 用 Sans-IO ヘルパーを再利用し、サーバー側の `reject_session` による非 2xx 送出 → クライアント受信後の `send()` のワイヤ送出 / イベントを確認する構成)
