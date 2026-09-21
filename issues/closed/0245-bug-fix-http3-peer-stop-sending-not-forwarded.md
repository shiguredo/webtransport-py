# http3 の高レベル層がピアの STOP_SENDING を nghttp3 へ伝えない

- Created: 2026-09-20
- Completed: 2026-09-21
- Branch: feature/fix-http3-peer-stop-sending-not-forwarded
- Polished: 2026-09-20

## 目的

`src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` は、低レベル QUIC 接続が返す `STOP_SENDING` の分岐を持たない。そのためピアがストリームの送信停止を要求しても nghttp3 に伝わらず、`nghttp3_conn_shutdown_stream_write` が呼ばれない。nghttp3 は当該ストリームの書き込み側を生存とみなし続け、`nghttp3_conn_writev_stream` がデータを返し続ける。QUIC 層では ngtcp2 が STOP_SENDING の受信時に RESET_STREAM を自動送出する (RFC 9000 Section 3.5 の MUST) ため、nghttp3 が返したデータは `NGTCP2_ERR_STREAM_SHUT_WR` で破棄される。低レベルの状態が実態 (送信方向が終端済み) と食い違ったまま残る。本 issue はこの転送を対象とし、アプリから送信不能を観測する手段の追加は対象外とする (追跡 issue は未起票)。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::recv_stop_sending_cb` は `STOP_SENDING` イベントを push する (RFC 9000 Section 19.5)。ngtcp2 はストリームが Ready / Send 状態なら RESET_STREAM を自動送出する
- `src/webtransport/http3/client.py` の `Client._quic_connection` と `src/webtransport/http3/server.py` の `ClientConnection.quic_connection` の型は低レベル `webtransport_ext.quic.Connection` であり、高レベル `quic.Client` / `quic.Server` を介さず `next_event()` を直接 drain する。したがって低レベル接続が返す `STOP_SENDING` イベントは高レベル層へ到達するが、分岐が無いため読み捨てられる
- `src/webtransport/http3/client.py` / `server.py` の QUIC イベント分岐にあるのは `STREAM_DATA` / `STREAM_RESET` / `CONNECTION_CLOSED` (server.py は `HANDSHAKE_COMPLETED` も) であり、`quic_low.EventType.STOP_SENDING` を参照する箇所が無い (`src/webtransport/h2/client.py` / `server.py` は `on_stop_sending` を持つ。`http2` 層には stop_sending 相当の API が無い)
- 低レベル `Http3Connection::shutdown_stream_write` は実装済みで (`nghttp3_conn_shutdown_stream_write` を呼び、`shutdown_stream_ids_` に記録する)、`src/webtransport/webtransport_ext/http3.pyi` にも公開されている。しかし `src/webtransport/` の Python 層からの呼び出しは 0 件であり、到達経路が無い
- `Http3Connection::send_data` は `shutdown_stream_ids_` に含まれるストリームを no-op にする契約を持つ。`shutdown_stream_write` が呼ばれないため、ピアの STOP_SENDING 後も `send_data` はデータを `stream_buffers_` に積み、`nghttp3_conn_resume_stream` で送出対象に戻す
- 実測した低レベル API の契約: `shutdown_stream_write` を呼ぶ前の `send_data` は送信バッファを作る (`_has_stream_buffer` が `True`)。`shutdown_stream_write` を呼んだ後の `send_data` は no-op になり、新たな送信バッファを作らない。ただし `submit_response` は応答ストリームのバッファを先に作るため、応答を flush しただけの状態ではシャットダウン後も `_has_stream_buffer` は `True` のままである (バッファが事前に解放されている場合に限り `None`)。`_has_stream_buffer` 単独では転送の有無を判別できない
- `src/bindings/quic.cpp` の `QuicConnection::send` は `ngtcp2_conn_writev_stream` の `NGTCP2_ERR_STREAM_SHUT_WR` で送信バッファを捨てるだけで、HTTP/3 層へは何も通知しない (同名の `QuicConnection::write_streams` は宣言のみで定義も呼び出しも無い)。ngtcp2 の参照実装 (`_deps/ngtcp2/1e4399d428ddcf510eca55b0f11306e333aa5194/source/examples/http3_client_proto_codec.cc`) は同じ箇所で `nghttp3_conn_shutdown_stream_write` を呼び、nghttp3 の書き込み側を閉じる
- RFC 9114 Section 4.1.1 は「実装は、まだ開いている方向を突然終了させることでリクエストをキャンセルする SHOULD」と述べ、送信方向のリセットと受信方向の読み取り中断を挙げている。受信方向の中断は 0240 で `shutdown_stream_read` を呼ぶ形で実装済みであり、送信方向側が未実装である

## 設計方針

- **採用案**: `src/webtransport/http3/client.py` と `server.py` の QUIC イベント drain に `quic_low.EventType.STOP_SENDING` の分岐を追加し、`Http3Connection.shutdown_stream_write(stream_id)` を呼ぶ。既存の `STREAM_RESET` 分岐 (`shutdown_stream_read` を呼ぶ) と同じ形であり、層の構成を変えない
  - ピアの STOP_SENDING は「自側の書き込み側の終了要求」であるため、読み取り側を閉じる `shutdown_stream_read` ではなく `shutdown_stream_write` を使う。読み取り側が既に終端している (`STREAM_RESET` を転送済み) 場合も、書き込み側の終了は独立して扱う (読み取り側の終端は `pending_headers_` / `stream_buffers_` の解放で表現され、書き込み側の記録 `shutdown_stream_ids_` とは別である)
  - `Http3Connection::shutdown_stream_write` の入力契約は変えない (接続が無い・閉じている場合は no-op)
  - RESET_STREAM の送出自体は ngtcp2 が行うため、高レベル層から改めて送出しない (0240 で `reset_stream` の再利用を避けたのと同じ理由)
  - アプリ向けのコールバックは追加しない。0220 は層間 API の非対称のうち「ストリーム終了の観測・ストリームの中断・再エクスポート」の 3 点を対象としており、`on_stop_sending` はその対象に含まれない (0220 の目的)。アプリから送信不能を観測する手段は 0250 (http3 の `on_stop_sending`) と 0251 (h3 のピア STOP_SENDING の扱い) が追跡する
- **不採用**: `src/bindings/quic.cpp` の `NGTCP2_ERR_STREAM_SHUT_WR` 経路で QUIC 層から HTTP/3 層へ通知する案。層をまたぐ新しい通知経路が必要になり、ピアの STOP_SENDING 以外 (アプリ起点の終了) と区別できない。原因が既知である以上、原因側で扱う
- `on_stream_reset` の扱い (0240 の `shutdown_stream_read` の転送) は変えない。stop_sending と reset_stream は独立した方向を扱う
- テストで「シャットダウンされたか」を判別するため、低レベル `Http3Connection` にテスト専用 API `_is_stream_write_shutdown` (`shutdown_stream_ids_` の参照) を追加する。既存の `_has_stream_buffer` は送信バッファが事前にある場合にシャットダウンを判別できないため (CODEBASE.md の「E2E テスト向けライブラリとして細粒度の機能を用意する」方針に沿う)
- 低レベル `Http3Connection::shutdown_stream_write` の契約 (以後の `send_data` が no-op、シャットダウン前に積んだ未送信データも送出されない) は `tests/test_http3_message_ext.py` の既存テスト 3 件で固定済みである。本 issue で低レベルのテストを追加する必要はない (`shutdown_stream_write` は `stream_buffers_` を消さないが、nghttp3 が SHUT_WR で当該ストリームを送出対象にしないため未送信データは送出されず、解放は既存のストリーム終了経路に任せる)
- 変更対象: `src/webtransport/http3/client.py` / `server.py`、`tests/test_e2e_http3_peer_stop_sending.py` (新規。0240 の `tests/test_e2e_http3_peer_reset.py` と同じ構成で実 QUIC ピアから STOP_SENDING を送る)、`skills/webtransport-py/SKILL.md` (`http3.Client` / `http3.Server` の説明に転送の契約を追記する)
- 0246 (同一 drain の `on_stream_reset` と `on_headers` の順序) が同じ QUIC イベント drain を触るため、実装の順序によっては rebase が必要になる

## 完了条件

- ピアが STOP_SENDING を送ると、DUT の低レベル `Http3Connection` で `shutdown_stream_write` が呼ばれ、以後そのストリームの `send_data` が no-op になる (新たな送信バッファを作らない)。判別の観測は次のいずれかを使う
  - DUT に `send_data` を呼んでも `_has_stream_buffer` が呼び出し前の値から変化しない (転送が無ければ `send_data` が新たにバッファを作るため変化する。基準値と比較すること。`submit_request` / `submit_response` が既にバッファを作っている場合は `True` のままで、値だけでは判別できない)
  - DUT が所有する `http3_connection` を run ループを止めた状態で直接駆動した場合、`send_data` の後に `get_streams_to_send` が当該ストリームを返さない (転送が無ければ返す。e2e で run ループを動かしたままだと送信待ちが毎周回 drain されるため、この観測は使えない)
  - `stream_writable` の値だけでは判別できない (未知ストリーム・フロー制御ブロック・読み取りブロックでも 0 になる)
- 実 QUIC ピアからの再現は DUT の側ごとに構成を分ける
  - サーバー側 DUT (`http3.Server`): ピアは `http3.Client` とし、低レベル `_quic_connection.stop_sending(stream_id, error_code)` と `_send_pending()` で STOP_SENDING だけを送出する (QPACK 符号化済みのリクエストで DUT の nghttp3 にストリームを作る必要があるため `quic.Client` は使わない)
  - クライアント側 DUT (`http3.Client`): ピアは `http3.Server` とし、その低レベル `quic_connection.stop_sending(...)` と `server._send_to(...)` で STOP_SENDING を送出する (0240 のクライアント側テストと同じ構成)。`quic.Client` は `http3.Client` のピアになれない (双方が QUIC クライアント)
  - 注意: ピアの `stop_sending` は `ngtcp2_conn_shutdown_stream_read` を呼ぶため、DUT の送信側ストリームがピアから見て終端済み (SHUT_RD かつ受信済みバイト数が final size に達している) だと 0 を返して STOP_SENDING を送出しない。DUT の送信側を終端しないこと (`Client.request` は FIN を送らないため、クライアント側 DUT のリクエストはそのままでよい。サーバー側 DUT では応答を `send_data(..., fin=False)` のままにする。転送後は `send_data(..., fin=True)` が no-op になり終端できないため、送信方向の終端は ngtcp2 の RESET_STREAM に任せる)
- 高レベル層の転送を外した状態で、上の判別の観測結果が変わることを実測で確認する (RED。低レベルの契約自体は `tests/test_http3_message_ext.py` の既存テストで固定済みなので、追加の低レベルテストは作らない)
- 全テストが通過する

## 対象外

- アプリ向けの `on_stop_sending` コールバックの追加 (0220 の対象はストリーム終了の観測・ストリームの中断・接続の終了・再エクスポートの 4 点であり、`on_stop_sending` は含まれない。0250 / 0251 が追跡する)
- ピアの RESET_STREAM の読み取り側の転送 (0240 で対応済み)
- STOP_SENDING を受けた後に同じストリームで送信を再開する API (RFC 9000 Section 3.5 / 19.4 により送信方向は終端し、再開できない)
- `src/webtransport/h3/` (WebTransport over HTTP/3) のピア STOP_SENDING の扱い。WebTransport のデータストリームも nghttp3 を介する (`H3Session` は `nghttp3_conn_read_stream2` / `nghttp3_conn_writev_stream` を使う) ため同種の欠陥が残るが、h3 層には書き込み側シャットダウン API が無く別対応になるため本 issue では扱わない (この経路を追跡する issue は未起票)

## 解決方法

- `src/webtransport/http3/client.py` と `server.py` の QUIC イベント drain に `quic_low.EventType.STOP_SENDING` の分岐を追加し、`Http3Connection.shutdown_stream_write(stream_id)` を呼ぶ。ピアの送信停止要求は自側の書き込み側の終了要求であるため、読み取り側を閉じる `shutdown_stream_read` (STREAM_RESET の分岐) とは独立して扱う。RESET_STREAM の送出は ngtcp2 に任せ、高レベル層からは送出しない
- 転送後は低レベルの `send_data` が no-op (送信対象を作らない) になり、nghttp3 の書き込み側も終了するため、nghttp3 が返したデータが破棄される経路が塞がる
- テストでシャットダウンを判別するため、低レベル `Http3Connection` にテスト専用 API `_is_stream_write_shutdown` を追加した。`shutdown_stream_ids_` を参照し、コネクションが無い・閉じている場合は false を返す (`frame_payload_left` と同じガード)。C++ ヘッダと stub に反映済み
- `tests/test_e2e_http3_peer_stop_sending.py` を追加した。サーバー側 DUT (`http3.Server`) はピアを `http3.Client`、クライアント側 DUT (`http3.Client`) はピアを `http3.Server` とし、それぞれ低レベルの `stop_sending` + `_send_pending()` / `_send_to` で STOP_SENDING だけを送出する。`_is_stream_write_shutdown` が真になること (転送) と、その後の `send_data` が `get_streams_to_send` に現れないこと (no-op) を表明する
- 完了条件がサーバー側のピアを `quic.Client` としていた点は `http3.Client` に変更した (QPACK 符号化済みのリクエストで DUT の nghttp3 にストリームを作る必要があるため。0240 が `quic.Client` を使ったのは RESET_STREAM だけを送るためであり、STOP_SENDING では同じ制約が無い)
- コメントの引用は RFC 9000 Section 3.5 の状態条件 (Ready / Send は MUST、Data Sent は MAY) と ngtcp2 の実装条件 (未 ACK の送信データが残っていれば自動送出) を書き分けた
- `skills/webtransport-py/SKILL.md` の `http3.Server` / `http3.Client` の節に、ピアの STOP_SENDING を書き込み側の終了として nghttp3 へ転送する契約を追記した
- RED は 2 通りで実測した: `client.py` / `server.py` の追加分岐を外す (git stash で develop 状態に戻す) と e2e 2 件が失敗し、低レベル `send_data` のシャットダウン判定を外す変異ではクライアント側テストの no-op 表明が失敗する
- `CHANGES.md` は CODEBASE.md の指示により更新しない。公開 API (nanobind) の追加はテスト専用 API `_is_stream_write_shutdown` のみで、型スタブは `make develop` の生成結果と一致させた
- 全テストが通過する (1319 passed)
