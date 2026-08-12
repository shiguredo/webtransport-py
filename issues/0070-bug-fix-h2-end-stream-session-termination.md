# HTTP/2 で WT_CLOSE_SESSION なしの END_STREAM のみによるセッション終了が検知されない

- Created: 2026-08-12
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-end-stream-session-termination
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/2 で、ピアが WT_CLOSE_SESSION カプセルを送らず END_STREAM フレームのみで CONNECT ストリームを閉じた場合 (draft-ietf-webtrans-http2-15 Section 3.4 の正規の終了経路) にセッション終了を検知できず、`send_datagram` がカプセルをワイヤへ送出し続ける問題を修正する。h3 側は `end_stream_cb` で FIN を検知して終了処理を行う経路を実装済み (CONNECT ストリームのクリーンクローズ対応) であり、h2 側に相当経路が存在しない。0063 の実装時にスコープ外として切り出した。

## 現状

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` は `NGHTTP2_HEADERS` フレームの `NGHTTP2_FLAG_END_STREAM` を検知しない (END_STREAM フラグのチェックがなく、`NGHTTP2_DATA` フレームの処理自体も存在しない)
- `on_stream_close_callback` はストリームの両ハーフが閉じたときにのみ発火する。ピアの END_STREAM のみでは half-closed (remote) のまま残り、エントリが削除されない
- 結果として、ピアが END_STREAM のみで終了したセッションはエントリ残存 + `is_established = true` + `is_terminated = false` のままとなり、`send_datagram` (0063 で実装済みのガード) をすり抜けてカプセルがワイヤへ送出され続ける。ピア側も `is_established = true` のままのため、受信したデータグラムを処理してしまう
- セッション終了の定義は draft-ietf-webtrans-http2-15 Section 3.4 (CONNECT ストリームのクローズ。WT_CLOSE_SESSION は終了前の通知であり必須ではない)

## 設計方針

- `on_frame_recv_callback` で `NGHTTP2_FLAG_END_STREAM` 付きの `NGHTTP2_HEADERS` / `NGHTTP2_DATA` フレームを受信した CONNECT ストリームの終了処理を行う (0063 で追加した `is_terminated` フラグを立て、`is_established` も false にする。既存の `handle_wt_close_session` の終了処理と対称にする)
- 変更対象は `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (`on_frame_recv_callback` の END_STREAM 検知)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- ピアが WT_CLOSE_SESSION なしで END_STREAM のみを送った場合、セッション終了が検知され `send_datagram` がカプセルをワイヤへ送出しない
- SessionClosed イベントが発火する (FIN 経路の error_code は 0。draft-ietf-webtrans-http3-16 Section 6 の「WT_CLOSE_SESSION なしのクリーンクローズは error code 0 と等価」の扱いを踏襲)
- 生存セッションの END_STREAM は検知されない (誤検知しない)
- モックなしの Sans-IO テストで検証できる (0063 で新設した h2 用 Sans-IO ヘルパーを再利用する)
