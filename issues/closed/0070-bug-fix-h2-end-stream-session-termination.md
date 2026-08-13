# HTTP/2 で WT_CLOSE_SESSION なしの END_STREAM のみによるセッション終了が検知されない

- Created: 2026-08-12
- Completed: 2026-08-12
- Branch: feature/fix-h2-end-stream-session-termination
- Polished: 2026-08-12

## 目的

WebTransport over HTTP/2 で、ピアが WT_CLOSE_SESSION カプセルを送らず END_STREAM フレームのみで CONNECT ストリームを閉じた場合 (draft-ietf-webtrans-http2-15 Section 3.4 の正規の終了経路) にセッション終了を検知できず、`send_datagram` がカプセルをワイヤへ送出し続ける問題を修正する。h3 側は `end_stream_cb` で FIN を検知して終了処理を行う経路を実装済み (CONNECT ストリームのクリーンクローズ対応) であり、h2 側に相当経路が存在しない。0063 の実装時にスコープ外として切り出した。

## 現状

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` は `NGHTTP2_HEADERS` フレームの `NGHTTP2_FLAG_END_STREAM` を検知しない (END_STREAM フラグのチェックがなく、`NGHTTP2_DATA` フレームの処理自体も存在しない)
- `on_stream_close_callback` はストリームの両ハーフが閉じたときにのみ発火する。ピアの END_STREAM のみでは half-closed (remote) のまま残り、エントリが削除されない
- 結果として、ピアが END_STREAM のみで終了したセッションはエントリ残存 + `is_established = true` + `is_terminated = false` のままとなり、`send_datagram` (0063 で実装済みのガード) をすり抜けてカプセルがワイヤへ送出され続ける

## 設計方針

- `on_frame_recv_callback` で `NGHTTP2_FLAG_END_STREAM` 付きの `NGHTTP2_HEADERS` / `NGHTTP2_DATA` フレームを受信した確立済みセッションの CONNECT ストリームの終了処理を行う: `is_terminated` フラグを立て `is_established` も false にし、SessionClosed イベント (error_code 0) を発火して `wt_sessions_` からエントリを削除する (h3 側の `close_stream` による `session_ids_` からの削除と対称)。エントリ削除により、以後の `on_stream_close_callback` / `close_session` / `send_datagram` / `send_stream_data` / `open_stream` / `reset_stream` がエントリ不在で自然に塞がる (END_STREAM 検知経路に限定。WT_CLOSE_SESSION 受信経路はエントリを削除しないため、受信後にアプリが `close_session` で応答すると両ハーフクローズで `on_stream_close_callback` が発火して SessionClosed が 2 回目に発火する既存の挙動が残る。本 issue のスコープ外。なお `stop_sending` / `drain_session` は `get_wt_session` を確認せずカプセルを送出するため塞がれないが、本 issue の対象外)。終了処理の共通ヘルパー化は行わない (既存の `handle_wt_close_session` の処理を並置する。唯一の違いはエントリ削除の有無であり、並置時にエントリ削除を欠落させると「エントリ不在で塞がる」前提が崩れるため注意する)
- END_STREAM 検知の対象は確立済み (`is_established = true`) のセッションに限定する: 非 2xx 拒否応答・201 応答はサーバー側 `reject_session` の data provider なし `nghttp2_submit_response` により END_STREAM 付き HEADERS で届く (0069 の対象) ため、確立済みでないエントリでは終了処理を行わない。0069 のエントリ削除実装後は非 2xx のエントリが存在しないため検知対象にならず、0069 の完了条件「拒否されたセッションに対して SessionClosed が発火しない」と整合する (実装順序は 0069 → 0070 を想定し、0069 実装前の非 2xx 応答の END_STREAM は検知対象外のままエントリが残る)。201 のエントリも `is_established = false` のまま残留する (0069 の既知の制約どおり)
- 既に `is_terminated` のセッション (WT_CLOSE_SESSION 受信済み・ローカル `close_session` 済み) の END_STREAM では終了処理をスキップする: コンプライアントなピアは WT_CLOSE_SESSION 送出後に必ず END_STREAM を送る (draft-ietf-webtrans-http2-15 Section 6.12 の MUST) ため、カプセル処理 (`handle_wt_close_session` による SessionClosed) の後に END_STREAM 検知が来る。スキップしないと SessionClosed が二重発火する (既存テスト `test_send_datagram_after_recv_wt_close_session_ignored` が 1 回のみの発火をピン留めしている。終了済みセッションは `is_established = false` のため「確立済み限定」の条件でもスキップされるが、防衛的に両条件を確認する)
- END_STREAM 検知のチェックは HEADERS フレームの処理分岐 (HCAT_REQUEST / HCAT_RESPONSE) の後に置く: クライアントが 200 + END_STREAM (受理と同時クローズ) を受けた場合、HCAT_RESPONSE 分岐で `is_established = true` が設定された後に検知しないと確立済み判定が成立しない (このケースでは SessionReady と SessionClosed が同一 `receive()` 内で連続発火する。正規の経路であり許容する)。チェックはフレーム種別 (cat) に依存させない (trailers 等の `NGHTTP2_HCAT_HEADERS` + END_STREAM も捕捉する)。`NGHTTP2_DATA` フレームの END_STREAM は、switch に `NGHTTP2_DATA` ケースを追加するか switch 後の共通部にチェックを置く (現状は default に落ちる)
- サーバー側の受理前 FIN (CONNECT リクエストの HEADERS に END_STREAM が付くケース) は確立済みでないため検知対象外となり、エントリが残留する (h3 側の受理前 FIN 対応 (0058 / 0064) の h2 版は本 issue のスコープ外)
- ピアの END_STREAM に対する自側の応答 (END_STREAM 送出) は行わない。ストリームは half-closed (remote) のまま接続終了まで残る (既知の制約として残す。draft-15 Section 6.12 の受信者側 MUST (END_STREAM で応答する) は WT_CLOSE_SESSION 受信時の応答についての規定であり、END_STREAM のみの受信には該当しない)
- 終了を学習する前に `http2_stream_buffers_` に積まれたカプセルは、エントリ削除後も flush されると送出され得る (0063 と同じ扱い。禁止対象は「新しいカプセル」であり、既にキュー済みの送出はスコープ外)
- 変更対象は `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (`on_frame_recv_callback` の END_STREAM 検知と `send_datagram` の docstring / 実装コメントの更新。「ピアが WT_CLOSE_SESSION なしで END_STREAM のみを送る終了経路...も検知できずスコープ外である (既知の制約)」という 0063 由来の記述を解消する)、`src/webtransport/h2/client.py` / `server.py` (`send_datagram` の docstring に END_STREAM 受信後の送出抑止を追記)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ。END_STREAM のみによる終了検知であることを文言で明記し、0063 のエントリと区別する)

## 完了条件

- ピアが WT_CLOSE_SESSION なしで END_STREAM のみを送った場合、セッション終了が検知され `send_datagram` がカプセルをワイヤへ送出しない
- SessionClosed イベントが 1 回だけ発火する (FIN 経路の error_code は 0 かつ error_message は空。draft-ietf-webtrans-http2-15 Section 6.12 の「Cleanly terminating a WebTransport session without a WT_CLOSE_SESSION capsule is semantically equivalent to terminating it with a WT_CLOSE_SESSION capsule that has an error code of 0 and an empty error string.」の扱いを踏襲)
- WT_CLOSE_SESSION + END_STREAM の両方を送るピア (コンプライアントな `close_session`) に対して SessionClosed が 1 回だけ発火する (自側が `close_session` で応答しない構成での確認。応答する構成では `on_stream_close_callback` 経由の 2 回目発火が既存の挙動として残る。既存テスト `test_send_datagram_after_recv_wt_close_session_ignored` がこの構成を既に検証している)
- エントリ削除が機能していることの間接検証として、END_STREAM 検知後に `close_session` / `send_stream_data` が no-op になることを確認する (エントリの削除自体は公開 API から直接観測できない)
- 検知対象外の END_STREAM で終了処理が実行されない (誤検知しない): 確立済みでないセッション (非 2xx 拒否・201 応答受信時・サーバー側の受理前 FIN) とエントリ不在のストリーム (通常の HTTP/2 ストリーム) の END_STREAM。WT データストリームの FIN (WT_STREAM_FIN カプセル) がセッション終了として誤検知されないことも確認する
- モックなしの Sans-IO テストで検証できる (0063 で新設した h2 用 Sans-IO ヘルパーを再利用する。「END_STREAM のみ」のシナリオは END_STREAM フラグ付きの DATA フレームのワイヤバイト列を `receive()` に直接注入する構成で再現する。`close_session` は WT_CLOSE_SESSION も送出するため使えない。HEADERS + END_STREAM (200 + END_STREAM 等) の検知と、エントリ不在の通常 HTTP/2 ストリームの誤検知なしは、HPACK 圧縮済みヘッダーブロックを含む HEADERS フレームのワイヤ注入で再現する (通常ストリームは先に HEADERS 注入で open にしてから DATA 注入する。0069 の 1xx 注入と同じ手法で、注入はテスト内の最後の操作にする等、HPACK 動的テーブルの非対称に配慮する))

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` の switch 後に共通の END_STREAM 検知を追加し、END_STREAM フラグ付きの HEADERS / DATA フレームを受信した確立済みセッションの CONNECT ストリームの終了処理 (`handle_end_stream`) を行うようにした: SessionClosed イベント (error_code 0、error_message 空) を発火し、`wt_sessions_` と `http2_stream_buffers_` からエントリを削除する (draft-ietf-webtrans-http2-15 Section 3.4 の正規の終了経路。WT_CLOSE_SESSION なしのクリーンクローズは error code 0 かつ空のエラー文字列の WT_CLOSE_SESSION と等価。Section 6.12)
- 検知対象は確立済み (`is_established = true`) のセッションに限定し、既に `is_terminated` のセッション (WT_CLOSE_SESSION 受信済み・ローカル `close_session` 済み) はスキップする (コンプライアントなピアの WT_CLOSE_SESSION + END_STREAM による二重発火の防止。`handle_wt_close_session` と並置し、共通ヘルパー化はしない)
- エントリ削除により、以後の `on_stream_close_callback` / `close_session` / `send_datagram` / `send_stream_data` / `open_stream` / `reset_stream` がエントリ不在で自然に塞がる (`stop_sending` / `drain_session` は対象外)。ピアの END_STREAM に対する自側の応答は行わない (既知の制約)
- `src/bindings/webtransport_h2.h` の `send_datagram` のドキュメントコメントと `src/bindings/webtransport_h2.cpp` の実装コメントを更新し、END_STREAM 検知による送出抑止を明記して「スコープ外 (既知の制約)」の旧記述を解消した。`src/webtransport/h2/client.py` / `server.py` の `send_datagram` docstring にも END_STREAM 受信後の送出抑止を追記した
- `tests/test_webtransport_h2_end_stream.py` を新規追加した (8 件)。END_STREAM のみでの終了検知と送出抑止・SessionClosed の 1 回発火 (error_code 0)・WT_CLOSE_SESSION + END_STREAM での二重発火なし・END_STREAM 検知後の `close_session` / `send_stream_data` の no-op・201 応答での誤検知なし・WT_STREAM_FIN での誤検知なし・通常 HTTP/2 ストリームの誤検知なし・200 + END_STREAM (受理と同時クローズ) での SESSION_READY と SessionClosed の連続発火・サーバー側の受理前 FIN での誤検知なし、をモックなしの Sans-IO 構成 (END_STREAM フラグ付き DATA / HEADERS フレームのワイヤ注入) で検証する
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した (END_STREAM のみによる終了検知であることを文言で明記し、0063 のエントリと区別した)
