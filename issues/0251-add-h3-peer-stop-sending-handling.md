# h3 の高レベル層がピアの STOP_SENDING を扱わない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-peer-stop-sending-handling
- Polished: {YYYY-MM-DD}

## 目的

`src/webtransport/h3/client.py` と `server.py` の QUIC イベント分岐に `quic.EventType.STOP_SENDING` が無いため、ピアがストリームの送信を止めてもアプリへ通知されず、nghttp3 側の書き込み側の状態も実態に合わないまま残る。WebTransport のデータストリームも nghttp3 を介する実装である (`H3Session` の受信は `nghttp3_conn_read_stream2`、送信は `nghttp3_conn_writev_stream`) ため、http3 層と同じ欠陥が h3 層にもある。0245 は http3 層のみを対象とし、h3 層は「同種の欠陥が残るが h3 層には書き込み側シャットダウン API が無く別対応」として対象外にしている (追跡 issue は未起票だった)。

## 現状

- h3 層の QUIC イベント分岐は `HANDSHAKE_COMPLETED` / `STREAM_DATA` / `DATAGRAM` / `STREAM_RESET` / `CONNECTION_CLOSED` であり、`quic.EventType.STOP_SENDING` の分岐が無い (受信ループと run ループの双方)
- h3 層は `on_stream_reset` を持つ (`h3.Client` は `on_stream_reset(stream_id: int, error_code: int | None)`、`h3.Server` は `on_stream_reset(session_id: int, stream_id: int, error_code: int | None, addr)` の形のコールバック) が、`on_stop_sending` は無い
- `h3_low.EventType.STOP_SENDING` (`h3/client.py` / `h3/server.py` にある分岐) は nghttp3 がピアへ STOP_SENDING を送るよう要求する逆方向のイベントであり、本 issue の対象 (ピアからの受信) とは別である
- 低レベル QUIC はピアの STOP_SENDING をイベントとして返す (`QuicConnection::recv_stop_sending_cb`)。quic 層と h2 層は 0162 / 0176 で同等のコールバックを持ち、http3 層は 0245 で転送を実装する予定である
- h3 層は高レベル `quic.Client` / `quic.Server` ではなく低レベル `webtransport_ext.quic.Connection` を直接 drain する
- `webtransport_ext/h3.pyi` の `Session` に shutdown 系のメソッドは無く、`H3Session::reset_stream` は `close_stream` への委譲である (書き込み側だけを止める API が無い)

## 設計方針

- **アプリ向けの通知**: `h3.Client` に `on_stop_sending(stream_id: int, error_code: int | None)`、`h3.Server` に `on_stop_sending(session_id: int, stream_id: int, error_code: int | None, addr)` の形のコールバックを追加する (`on_stream_reset` の形と `error_code` の変換規則に揃える)。STOP_SENDING を受けたストリームが WebTransport のデータストリームであるかは `Session.stream_wt_session_id(stream_id)` で解決し、解決できないストリーム (CONNECT ストリームや WebTransport 以外) では通知しない (`on_stream_end` と同じ扱い)
- **書き込み側の後始末**: nghttp3 には `nghttp3_conn_shutdown_stream_write` があり、http3 層は `Http3Connection::shutdown_stream_write` として公開している。h3 層のデータストリームも nghttp3 の管理下であるため、`H3Session` に同等の shutdown を追加して伝える案を第一候補とする (依存ライブラリの変更は不要)
  - **不採用**: `H3Session::reset_stream` / `close_stream` の再利用。ピアが送信を止めただけのストリームをアプリ起点のリセットとして扱うと、`ResetStream` イベントの push と QUIC RESET_STREAM の送出が起きる (0240 が http3 層で避けたのと同じ理由)
  - QUIC の RESET_STREAM 送出は ngtcp2 が行う (RFC 9000 Section 3.5 の MUST) ため、高レベル層から改めて送出しない
- 通知と転送の順序は http3 層 (0245) と同じにする (転送を先に行い、その後にアプリへ通知する)
- 変更対象: `src/bindings/webtransport_h3.cpp` / `.h` (`H3Session` の書き込み側シャットダウン)、`src/webtransport/webtransport_ext/h3.pyi` (再生成)、`src/webtransport/h3/client.py` / `server.py`、`tests/`、`skills/webtransport-py/SKILL.md`

## 完了条件

- ピアが STOP_SENDING を送ると、`on_stop_sending` が 1 回呼ばれ、`h3.Client` では (stream_id, error_code)、`h3.Server` では (session_id, stream_id, error_code, addr) が渡される (`error_code` の変換規則は `on_stream_reset` に揃える)
- WebTransport 以外のストリームと CONNECT ストリームでは通知しない (対照)
- 書き込み側の終了が nghttp3 へ伝わる (以後そのストリームのデータが送出されないことをワイヤまたは低レベル層の観測で確認する)
- 既存の `on_stream_reset` / `on_stream_end` の挙動が変わらない
- 分岐の追加を外すとテストが失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- http3 層の同等の対応 (0245 の転送と 0250 のコールバックで扱う)
- セッション制御ストリーム (CONNECT) の STOP_SENDING の扱い
- アプリ起点の `reset_stream` / `close_stream` の挙動 (現状維持)
- nghttp3 本体の変更 (バインディング層で吸収する。CODEBASE.md の「nghttp2 / nghttp3 / ngtcp2 をフォークしないこと」)
- 送信を再開する API (RFC 9000 Section 3.5 / 19.4 により送信方向は終端し、再開できない)
