# h3 の高レベル層がピアの STOP_SENDING を扱わない

- Created: 2026-09-20
- Completed: 2026-10-14
- Branch: feature/add-h3-peer-stop-sending-handling
- Polished: 2026-10-04
- Updated: 2026-10-05

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
  - **不採用**: `H3Session::reset_stream` / `close_stream` の再利用。両者は `nghttp3_conn_close_stream` でストリーム全体 (受信方向も含む) を破棄し、以後ピアから届くデータを落とす。STOP_SENDING は送信方向のみの終端であり、受信方向は生存している (ピアが送信し続ける DATA は `on_stream_data` で届き続けるべきである)。加えて `reset_stream` は `map_send_error_code` によるリマップと `close_stream` 相当の後始末 (stream_info_ / 送信バッファの解放) を伴い、アプリ起点のリセットと同じ意味になる。http3 層 (0240) が避けた「返送」は、STOP_SENDING を受けたデータストリームに対しては起きない (`H3Session::close_stream` はデータストリームでは `ResetStream` イベントを push せず `STREAM_CLOSED` のみを push する。nghttp3 の `reset_stream_cb` は `nghttp3_conn_abort_stream` からのみ発火するため)。ただし CONNECT ストリームの `close_stream` では nghttp3 内部 (`conn_unlink_wt_session`) が所属データストリームを `WT_SESSION_GONE` で破棄して `ResetStream` / `StopSending` を発火させる (`src/bindings/webtransport_h3.cpp` の `close_stream` のコメント参照)。本 issue は CONNECT ストリームを対象外とするため結論は変わらず、不採用の理由は返送ではなく全ストリーム破棄である
  - QUIC の RESET_STREAM 送出は ngtcp2 が行う (RFC 9000 Section 3.5 は Ready / Send 状態での送出を MUST、Data Sent 状態での延期を MAY と定めており、ngtcp2 が自動送出する) ため、高レベル層から改めて送出しない
- 通知と転送の順序は http3 層と同じにする (転送を先に行い、その後にアプリへ通知する。http3 層の転送は 0245 で実装済み、通知は 0250 が未実装であり、0250 の設計方針がこの順序を定めている)
- 0220 (h3 / h2 の `on_stream_end` 追加) と 0257 (h3 のセッション終了 API と `stop_sending` 送出 API) は同じ `src/webtransport/h3/{client,server}.py` のイベント分岐と `skills/webtransport-py/SKILL.md` を触るため、実装順によっては rebase が必要になる (0220 / 0257 の両 issue にも同じ申し送りがある。本 issue は `on_stream_end` の実装自体には依存しない)
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

## 解決方法

- `src/bindings/webtransport_h3.h` / `.cpp` の `H3Session` に `shutdown_stream_write(stream_id)` を追加した。nghttp3 に書き込み側の終了 (SHUT_WR) を伝え、`shutdown_stream_ids_` に記録してシャットダウン済みストリームへの `send_stream_data` を no-op にする (no-op にしないと未送信データが `stream_buffers_` に積み上がったまま残る)。接続が無い・閉じている場合は no-op (`Http3Connection::shutdown_stream_write` と同じ入力契約)。記録は `close_stream`、`stream_close_cb`、`erase_session_streams` で除去する
- `src/webtransport/_h3_client.py` の `Client` と `src/webtransport/_h3_server.py` の `Server` に `on_stop_sending` を追加した。形と `error_code` の変換規則は `on_stream_reset` に揃え、`deliver_stream_reset_error_code` を使う (通知対象はデータストリームのみのため `is_connect_stream` は常に False)。QUIC の `STOP_SENDING` イベントで `Session.stream_wt_session_id` がセッション ID を返すストリームだけを対象に、`shutdown_stream_write` で転送してから通知する。CONNECT ストリームと WebTransport 以外のストリームは転送も通知もしない
- 確立中の `connect()` 内ループは変更していない (確立前は CONNECT ストリームしか存在せず、対象外のため)
- 変更対象にあった `src/webtransport/h3/client.py` / `server.py` は現存しない。高レベル h3 層は `src/webtransport/_h3_client.py` / `_h3_server.py` にあり (`webtransport.h3` は Sans-IO の低レベル API のみ)、そちらへ実装した
- テスト: `tests/test_e2e_webtransport_h3_peer_stop_sending.py` を追加した。サーバー側 DUT はピアを `Client`、クライアント側 DUT はピアを `Server` とし、低レベルの `stop_sending` で STOP_SENDING だけを送出する。通知の引数、`error_code` の変換 (レンジ内はアプリコード、レンジ外は None)、`stream_writable` が 0 になること (転送)、`send_stream_data` がバッファも積まないこと (no-op) を表明する。対照は 2 つあり、DUT が開いた WT 管理外の双方向ストリームには WT ヘッダーだけをワイヤで送ってピアにストリームを持たせ (ピアの ngtcp2 はストリームを持たないストリームへの `shutdown_stream_read` を黙って無視するため、送らないと検証が空振りする)、CONNECT ストリームでは nghttp3 の書き込み側が生存のままであることも表明する。`tests/test_webtransport_h3_stream_control.py` には低レベル契約 (シャットダウン後は送出もバッファもしない、シャットダウン前に積んだデータも送出されない) を追加した
- RED は 2 通りで実測した。Python の追加分岐を外すと e2e 2 件が失敗し、C++ の no-op ガードを外して再ビルドすると低レベルの no-op 表明が失敗する
- `skills/webtransport-py/SKILL.md` の h3 クライアント / サーバーの節と Sans-IO `Session` の節を更新した。統一 API の `Server.on_stop_sending` は HTTP/2 のみのままで、HTTP/3 では `server.h3` の `on_stop_sending` を使う (`on_error` と同じくプロトコル固有 API を `h3` / `h2` から呼ぶ形)
- `CHANGES.md` は CODEBASE.md の指示により更新しない。型スタブは `make develop` の生成結果と一致させた (公開 API として `shutdown_stream_write` が追加される)
- 全テストが通過する (1472 passed)
