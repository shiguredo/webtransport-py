# h3 の高レベル層がピアの STOP_SENDING を扱わない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-peer-stop-sending-handling
- Polished: 2026-10-04

## 目的

`src/webtransport/h3/client.py` と `server.py` の QUIC イベント分岐に `quic.EventType.STOP_SENDING` が無いため、ピアがストリームの送信を止めてもアプリへ通知されず、nghttp3 側の書き込み側の状態も実態に合わないまま残る。WebTransport のデータストリームも nghttp3 を介する実装である (`H3Session` の受信は `nghttp3_conn_read_stream2`、送信は `nghttp3_conn_writev_stream`) ため、http3 層と同じ欠陥が h3 層にもある。0245 は http3 層のみを対象とし、h3 層は「同種の欠陥が残るが h3 層には書き込み側シャットダウン API が無く別対応」として対象外にしている (追跡 issue は未起票だった)。

## 現状

- h3 層の QUIC イベント分岐は `HANDSHAKE_COMPLETED` / `STREAM_DATA` / `DATAGRAM` / `STREAM_RESET` / `CONNECTION_CLOSED` であり、`quic.EventType.STOP_SENDING` の分岐が無い (受信ループと run ループの双方)
- h3 層は `on_stream_reset` を持つ (`h3.Client` は `on_stream_reset(stream_id: int, error_code: int | None)`、`h3.Server` は `on_stream_reset(session_id: int, stream_id: int, error_code: int | None, addr)` の形のコールバック) が、`on_stop_sending` は無い
- `h3_low.EventType.STOP_SENDING` (`h3/client.py` / `h3/server.py` にある分岐) は nghttp3 がピアへ STOP_SENDING を送るよう要求する逆方向のイベントであり、本 issue の対象 (ピアからの受信) とは別である
- 低レベル QUIC はピアの STOP_SENDING をイベントとして返す (`QuicConnection::recv_stop_sending_cb`)。quic 層と h2 層は 0162 / 0176 で同等のコールバックを持ち、http3 層は 0245 で nghttp3 への転送が実装済みである (ピアの STOP_SENDING をアプリへ通知するコールバックは 0250 が扱う)
- h3 層は高レベル `quic.Client` / `quic.Server` ではなく低レベル `webtransport_ext.quic.Connection` を直接 drain する
- `webtransport_ext/h3.pyi` の `Session` に shutdown 系のメソッドは無く、`H3Session::reset_stream` は `close_stream` への委譲である (書き込み側だけを止める API が無い)

## 設計方針

- **アプリ向けの通知**: `h3.Client` に `on_stop_sending(stream_id: int, error_code: int | None)`、`h3.Server` に `on_stop_sending(session_id: int, stream_id: int, error_code: int | None, addr)` の形のコールバックを追加する (形と `error_code` の変換規則は `on_stream_reset` に揃え、`deliver_stream_reset_error_code` と同じ変換を使う。通知対象がデータストリームのみのため `is_connect_stream` は常に False)。STOP_SENDING を受けたストリームが WebTransport のデータストリームであるかは `Session.stream_wt_session_id(stream_id)` で解決し、解決できないストリーム (CONNECT ストリームや WebTransport 以外) では通知しない。この除外規則は 0220 が h3 に追加する `on_stream_end` と同じ規則 (セッション ID を解決できないストリームは通知しない) であり、h3 の `on_stream_end` はまだ実装されていないため、本 issue では規則を直接規定する
- **書き込み側の後始末**: nghttp3 には `nghttp3_conn_shutdown_stream_write` があり、http3 層は `Http3Connection::shutdown_stream_write` として公開している。h3 層のデータストリームも nghttp3 の管理下であるため、`H3Session::shutdown_stream_write(stream_id)` を追加して伝える案を第一候補とする (依存ライブラリの変更は不要。入力契約は `Http3Connection::shutdown_stream_write` と同じ: 接続が無い・閉じている場合は no-op)。シャットダウン済みストリームを記録し、以後の `H3Session::send_stream_data` を no-op にする (http3 層の `shutdown_stream_ids_` と同じ。no-op にしないと nghttp3 が SHUT_WR で当該ストリームを送出対象にしないため、バッファが積み上がったまま残る)
  - **不採用**: `H3Session::reset_stream` / `close_stream` の再利用。両者は `nghttp3_conn_close_stream` でストリーム全体 (受信方向も含む) を破棄し、以後ピアから届くデータを落とす。STOP_SENDING は送信方向のみの終端であり、受信方向は生存している (ピアが送信し続ける DATA は `on_stream_data` で届き続けるべきである)。加えて `reset_stream` は `map_send_error_code` によるリマップと `close_stream` 相当の後始末 (stream_info_ / 送信バッファの解放) を伴い、アプリ起点のリセットと同じ意味になる。http3 層 (0240) が避けた「返送」はこの層では起きない (`H3Session::close_stream` は `ResetStream` イベントを push しない。nghttp3 の `reset_stream_cb` は `nghttp3_conn_abort_stream` のみが発火させるため) ので、不採用の理由は返送ではなく全ストリーム破棄である
  - QUIC の RESET_STREAM 送出は ngtcp2 が行う (RFC 9000 Section 3.5 の MUST) ため、高レベル層から改めて送出しない
- 通知と転送の順序は http3 層と同じにする (転送を先に行い、その後にアプリへ通知する。http3 層は 0245 の転送と 0250 の通知がこの順序である)
- 0220 (h3 / h2 の `on_stream_end` 追加) は同じ `src/webtransport/h3/{client,server}.py` のイベント分岐を触るため、実装順によっては rebase が必要になる (0220 の設計方針と同じ申し送り。本 issue は `on_stream_end` の実装自体には依存しない)
- 変更対象: `src/bindings/webtransport_h3.cpp` / `.h` (`H3Session` の書き込み側シャットダウン)、`src/webtransport/webtransport_ext/h3.pyi` (再生成)、`src/webtransport/h3/client.py` / `server.py`、`tests/`、`skills/webtransport-py/SKILL.md`

## 完了条件

- ピアが STOP_SENDING を送ると、`on_stop_sending` が 1 回呼ばれ、`h3.Client` では (stream_id, error_code)、`h3.Server` では (session_id, stream_id, error_code, addr) が渡される (`error_code` の変換規則は `on_stream_reset` に揃える)
- WebTransport 以外のストリームと CONNECT ストリームでは通知しない (対照)。CONNECT ストリームへの nghttp3 の転送 (shutdown_stream_write) も行わない
- 書き込み側の終了が nghttp3 へ伝わる (以後そのストリームのデータが送出されないことをワイヤまたは低レベル層の観測で確認する。シャットダウン済みストリームへの `send_stream_data` が no-op になることを含む)
- 既存の `on_stream_reset` の挙動が変わらない (h3 の `on_stream_end` は 0220 の対象であり、本 issue では追加も変更もしない)
- 分岐の追加を外すとテストが失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- http3 層の同等の対応 (0245 の転送と 0250 のコールバックで扱う)
- セッション制御ストリーム (CONNECT) の STOP_SENDING の扱い (通知も nghttp3 への転送も対象としない。セッション終了の経路 (close_session による WT_CLOSE_SESSION の送出等) と絡み、CONNECT ストリームの書き込み側を終端したときの挙動を別途定める必要があるため。追跡 issue は未起票)
- アプリ起点の `reset_stream` / `close_stream` の挙動 (現状維持)
- nghttp3 本体の変更 (バインディング層で吸収する。CODEBASE.md の「nghttp2 / nghttp3 / ngtcp2 をフォークしないこと」)
- 送信を再開する API (RFC 9000 Section 3.5 / 19.4 により送信方向は終端し、再開できない)
