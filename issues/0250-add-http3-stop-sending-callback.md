# http3 の高レベル層にピアの STOP_SENDING を通知するコールバックが無い

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/add-http3-stop-sending-callback
- Polished: 2026-10-03

## 目的

`http3.Client` / `http3.Server` にはピアの STOP_SENDING をアプリへ通知するコールバックが無く、アプリは「ピアがストリームの送信を止めた」ことを観測できない。0245 はピアの STOP_SENDING を nghttp3 へ転送して低レベル層の状態を実態に合わせるが、アプリ向けの通知は対象外としている。`quic` 層 (`quic.Client` / `quic.Server`) と `h2` 層 (`h2.Client` / `h2.Server`) は同等の `on_stop_sending` を持ち、0162 (quic 層) と 0176 (h2 層) で層ごとに追加されてきた。`http2` 層には stop_sending 相当の API 自体が無く、0220 が `on_stop_sending` を対象外としているため、残る層は http3 と h3 である (h3 は 0251 で扱う)。

## 現状

- `src/webtransport/http3/client.py` の `Client.run` と `src/webtransport/http3/server.py` の `Server._drain_quic_events` の QUIC イベント分岐は `STREAM_DATA` / `STREAM_RESET` / `STOP_SENDING` / `CONNECTION_CLOSED` (server.py は `HANDSHAKE_COMPLETED` も) であり、`STOP_SENDING` では `Http3Connection.shutdown_stream_write(stream_id)` を呼ぶ (0245 で実装済み)。アプリへの通知が無いのはこの分岐に `on_stop_sending` の呼び出しが無いためである
- 低レベルではピアの STOP_SENDING は `webtransport_ext.quic.Connection.next_event()` が返す `quic_low.EventType.STOP_SENDING` として到達する。http3 層は高レベル `quic.Client` / `quic.Server` を介さず低レベル接続を直接 drain するため、0245 が追加した分岐で既に受け取れており、転送 (shutdown_stream_write) は実装済みである
- コールバックの形の既存例は `Client.on_stream_reset(stream_id, error_code)` と `Server.on_stream_reset(stream_id, error_code, addr)` である
- `skills/webtransport-py/SKILL.md` の HTTP/3 節に `http3.Client` / `http3.Server` のコールバック一覧がある
- 0162 が quic 層、0176 が h2 層で同種の追加を行っており、本 issue は同じ形の追加である

## 設計方針

- `Client.on_stop_sending(stream_id, error_code)` と `Server.on_stop_sending(stream_id, error_code, addr)` の setter を追加する。形は `on_stream_reset` に揃える
- 呼び出し位置は QUIC の `STOP_SENDING` 分岐 (0245 が追加済み) の `shutdown_stream_write` の後とする。ただしアプリへの通知は 0246 と同じく保留し、同一バッチの HTTP/3 イベント drain を終えた後 (`on_stream_reset` と同じ位置、`on_stream_end` の通知より前) に到着順で通知する。その場で `await` すると、同一バッチに先に到着した `HEADERS` / `DATA` の通知より先に STOP_SENDING が届き、0246 が修正した順序逆転が再発する (`on_stream_reset` はこの順序を保つため保留している)
- コールバック未設定時は何もしない。`await` の形と例外の伝播は既存の `on_stream_reset` と同じにする
- 通知は引数のみを渡し、コールバックの呼び出しで層の状態を変えない (転送は 0245 の責務)
- docstring と `skills/webtransport-py/SKILL.md` の HTTP/3 節のコールバック一覧を更新する (`tests/test_skill_api_consistency.py` は SKILL から実装の方向しか検査しないため、実装から SKILL への追記漏れは人手で確認する)。あわせて、SKILL の HTTP/3 節の `on_stream_end` の説明にある「RESET_STREAM / STOP_SENDING で終了した場合は呼ばれず `on_stream_reset` が担う」を、STOP_SENDING では本 issue の `on_stop_sending` が担う形に直す (STOP_SENDING は STREAM_RESET イベントを発火しないため現行の記述では通知先が消える)
- `src/bindings/` の変更は不要 (低レベルは既にイベントを push している)
- 変更対象: `src/webtransport/http3/client.py` / `server.py`、`tests/test_e2e_http3_peer_stop_sending.py` (0245 が作成済みの足場を拡張する)、`skills/webtransport-py/SKILL.md`

## 完了条件

- ピアが STOP_SENDING を送ると、`on_stop_sending` が 1 回呼ばれ、引数が `http3.Client` では (stream_id, error_code)、`http3.Server` では (stream_id, error_code, addr) になる
- 0245 の転送 (以後の `send_data` が no-op になること) と同じ受信で両方を観測できる
- 同一の受信バッチに完備した HEADERS / DATA と STOP_SENDING が並ぶ場合は、先に到着した HEADERS / DATA のコールバックが `on_stop_sending` より先に呼ばれる (0246 の同一バッチ構成を再利用して検証し、保留を外すと失敗することを実測で確認する RED に含める)
- コールバック未設定でも例外が起きない
- `skills/webtransport-py/SKILL.md` の HTTP/3 節のコールバック一覧に追加されている
- 通知の追加を外すとテストが失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- h3 層 (WebTransport over HTTP/3) の同等の追加 (0251 で扱う)
- nghttp3 への転送そのもの (0245 で扱う)
- 送信停止後のストリームの後始末の変更 (0245 の範囲)
- `http2` 層への追加 (stop_sending 相当の API 自体が無い。0220 の方針)
- 送信を再開する API (RFC 9000 Section 3.5 / 19.4 により送信方向は終端し、再開できない)
