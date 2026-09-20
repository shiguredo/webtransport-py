# http3 の高レベル層がピアの STOP_SENDING を nghttp3 へ伝えない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
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
  - アプリ向けのコールバックは追加しない。0220 は層間 API の非対称のうち「ストリーム終了の観測・ストリームの中断・再エクスポート」の 3 点を対象としており、`on_stop_sending` はその対象に含まれない (0220 の目的)。本 issue は nghttp3 への転送だけを対象とし、アプリから送信不能を観測する手段の追加は追跡 issue が無い (未起票)
- **不採用**: `src/bindings/quic.cpp` の `NGTCP2_ERR_STREAM_SHUT_WR` 経路で QUIC 層から HTTP/3 層へ通知する案。層をまたぐ新しい通知経路が必要になり、ピアの STOP_SENDING 以外 (アプリ起点の終了) と区別できない。原因が既知である以上、原因側で扱う
- `on_stream_reset` の扱い (0240 の `shutdown_stream_read` の転送) は変えない。stop_sending と reset_stream は独立した方向を扱う
- 低レベル `Http3Connection::shutdown_stream_write` の契約 (以後の `send_data` が no-op、シャットダウン前に積んだ未送信データも送出されない) は `tests/test_http3_message_ext.py` の既存テスト 3 件で固定済みである。本 issue で低レベルのテストを追加する必要はない (`shutdown_stream_write` は `stream_buffers_` を消さないが、nghttp3 が SHUT_WR で当該ストリームを送出対象にしないため未送信データは送出されず、解放は既存のストリーム終了経路に任せる)
- 変更対象: `src/webtransport/http3/client.py` / `server.py`、`tests/test_e2e_http3_peer_stop_sending.py` (新規。0240 の `tests/test_e2e_http3_peer_reset.py` と同じ構成で実 QUIC ピアから STOP_SENDING を送る)、`skills/webtransport-py/SKILL.md` (`http3.Client` / `http3.Server` の説明に転送の契約を追記する)
- 0246 (同一 drain の `on_stream_reset` と `on_headers` の順序) が同じ QUIC イベント drain を触るため、実装の順序によっては rebase が必要になる

## 完了条件

- ピアが STOP_SENDING を送ると、DUT の低レベル `Http3Connection` で `shutdown_stream_write` が呼ばれ、以後そのストリームの `send_data` が no-op になる (新たな送信バッファを作らない)。判別の観測は次のいずれかを使う
  - DUT に `send_data` を呼んでも `_has_stream_buffer` が呼び出し前の値から変化しない (転送が無ければ `send_data` が新たにバッファを作るため変化する。基準値と比較すること。`submit_request` / `submit_response` が既にバッファを作っている場合は `True` のままで、値だけでは判別できない)
  - DUT が所有する `http3_connection` を run ループを止めた状態で直接駆動した場合、`send_data` の後に `get_streams_to_send` が当該ストリームを返さない (転送が無ければ返す。e2e で run ループを動かしたままだと送信待ちが毎周回 drain されるため、この観測は使えない)
  - `stream_writable` の値だけでは判別できない (未知ストリーム・フロー制御ブロック・読み取りブロックでも 0 になる)
- 実 QUIC ピアからの再現は DUT の側ごとに構成を分ける
  - サーバー側 DUT (`http3.Server`): ピアは `quic.Client` とし、低レベル `_connection.stop_sending(stream_id, error_code)` と `_send_pending()` で STOP_SENDING だけを送出する (0240 のサーバー側 e2e と同じ手順)
  - クライアント側 DUT (`http3.Client`): ピアは `http3.Server` とし、その低レベル `quic_connection.stop_sending(...)` と `server._send_to(...)` で STOP_SENDING を送出する (0240 のクライアント側テストと同じ構成)。`quic.Client` は `http3.Client` のピアになれない (双方が QUIC クライアント)
  - 注意: ピアの `stop_sending` は `ngtcp2_conn_shutdown_stream_read` を呼ぶため、DUT の送信側ストリームがピアから見て終端済み (SHUT_RD かつ受信済みバイト数が final size に達している) だと 0 を返して STOP_SENDING を送出しない。DUT の送信側を終端しないこと (`Client.request` は FIN を送らないため、クライアント側 DUT のリクエストはそのままでよい。サーバー側 DUT では応答を `send_data(..., fin=False)` のままにする。転送後は `send_data(..., fin=True)` が no-op になり終端できないため、送信方向の終端は ngtcp2 の RESET_STREAM に任せる)
- 高レベル層の転送を外した状態で、上の判別の観測結果が変わることを実測で確認する (RED。低レベルの契約自体は `tests/test_http3_message_ext.py` の既存テストで固定済みなので、追加の低レベルテストは作らない)
- 全テストが通過する

## 対象外

- アプリ向けの `on_stop_sending` コールバックの追加 (0220 の対象はストリーム終了の観測・ストリームの中断・再エクスポートの 3 点であり、`on_stop_sending` は含まれない。追跡 issue も無い。未起票)
- ピアの RESET_STREAM の読み取り側の転送 (0240 で対応済み)
- STOP_SENDING を受けた後に同じストリームで送信を再開する API (RFC 9000 Section 3.5 / 19.4 により送信方向は終端し、再開できない)
- `src/webtransport/h3/` (WebTransport over HTTP/3) のピア STOP_SENDING の扱い。WebTransport のデータストリームも nghttp3 を介する (`H3Session` は `nghttp3_conn_read_stream2` / `nghttp3_conn_writev_stream` を使う) ため同種の欠陥が残るが、h3 層には書き込み側シャットダウン API が無く別対応になるため本 issue では扱わない (この経路を追跡する issue は未起票)
