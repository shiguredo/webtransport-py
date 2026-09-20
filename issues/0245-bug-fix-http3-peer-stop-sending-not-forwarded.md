# http3 の高レベル層がピアの STOP_SENDING を nghttp3 へ伝えない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-peer-stop-sending-not-forwarded
- Polished: {YYYY-MM-DD}

## 目的

`src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` は、低レベル QUIC 接続が返す `STOP_SENDING` の分岐を持たない。そのためピアがストリームの送信停止を要求しても nghttp3 に伝わらず、`nghttp3_conn_shutdown_stream_write` が呼ばれない。nghttp3 は当該ストリームの書き込み側を生存とみなし続け、`nghttp3_conn_writev_stream` がデータを返し続ける。QUIC 層では ngtcp2 が STOP_SENDING の受信時に RESET_STREAM を自動送出する (RFC 9000 Section 3.5 の MUST) ため、nghttp3 が返したデータは `NGTCP2_ERR_STREAM_SHUT_WR` で破棄される。結果として、アプリの `send_data` は成功を返すのにデータはピアへ届かず、送信不能になったことをアプリから観測できない。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::recv_stop_sending_cb` は `STOP_SENDING` イベントを push する (RFC 9000 Section 19.5)。ngtcp2 はストリームが Ready / Send 状態なら RESET_STREAM を自動送出する
- `src/webtransport/http3/client.py` の `Client._quic_connection` と `src/webtransport/http3/server.py` の `ServerClient.quic_connection` の型は低レベル `webtransport_ext.quic.Connection` であり、高レベル `quic.Client` / `quic.Server` を介さず `next_event()` を直接 drain する。したがって低レベル接続が返す `STOP_SENDING` イベントは高レベル層へ到達するが、分岐が無いため読み捨てられる
- `src/webtransport/http3/client.py` / `server.py` の QUIC イベント分岐にあるのは `STREAM_DATA` / `STREAM_RESET` / `CONNECTION_CLOSED` であり、`quic_low.EventType.STOP_SENDING` を参照する箇所が無い (`src/webtransport/h2/client.py` / `server.py` と `http2` 層は `on_stop_sending` を持つ)
- 低レベル `Http3Connection::shutdown_stream_write` は実装済みで (`nghttp3_conn_shutdown_stream_write` を呼び、`shutdown_stream_ids_` に記録する)、`src/webtransport/webtransport_ext/http3.pyi` にも公開されている。しかし `src/webtransport/` の Python 層からの呼び出しは 0 件であり、到達経路が無い
- `Http3Connection::send_data` は `shutdown_stream_ids_` に含まれるストリームを no-op にする契約を持つ。`shutdown_stream_write` が呼ばれないため、ピアの STOP_SENDING 後も `send_data` はデータを `stream_buffers_` に積み、`nghttp3_conn_resume_stream` で送出対象に戻す
- 実測した低レベル API の契約 (転送を実装した後に観測できる状態): `shutdown_stream_write` を呼ぶ前の `send_data` は送信バッファを作り `_has_stream_buffer` が `True` になる。`shutdown_stream_write` を呼んだ後の `send_data` は no-op になり `_has_stream_buffer` が `None` のままになる。転送が無い現状ではこの no-op 経路に入らない
- `src/bindings/quic.cpp` の `QuicConnection::write_streams` は `ngtcp2_conn_writev_stream` の `NGTCP2_ERR_STREAM_SHUT_WR` で送信バッファを捨てるだけで、HTTP/3 層へは何も通知しない。ngtcp2 の参照実装 (`_deps/ngtcp2/1e4399d428ddcf510eca55b0f11306e333aa5194/source/examples/http3_client_proto_codec.cc`) は同じ箇所で `nghttp3_conn_shutdown_stream_write` を呼び、nghttp3 の書き込み側を閉じる
- RFC 9114 Section 4.1.1 は「実装は、まだ開いている方向を突然終了させることでリクエストをキャンセルする SHOULD」と述べ、送信方向のリセットと受信方向の読み取り中断を挙げている。受信方向の中断は 0240 で `shutdown_stream_read` を呼ぶ形で実装済みであり、送信方向側が未実装である

## 設計方針

- **採用案**: `src/webtransport/http3/client.py` と `server.py` の QUIC イベント drain に `quic_low.EventType.STOP_SENDING` の分岐を追加し、`Http3Connection.shutdown_stream_write(stream_id)` を呼ぶ。既存の `STREAM_RESET` 分岐 (`shutdown_stream_read` を呼ぶ) と同じ形であり、層の構成を変えない
  - ピアの STOP_SENDING は「自側の書き込み側の終了要求」であるため、読み取り側を閉じる `shutdown_stream_read` ではなく `shutdown_stream_write` を使う。既に読み取り側が閉じている (`shutdown_stream_ids_` ではなく `STREAM_RESET` 経由) 場合も、書き込み側の終了は独立して扱う
  - `Http3Connection::shutdown_stream_write` の入力契約は変えない (接続が無い・閉じている場合は no-op)
  - RESET_STREAM の送出自体は ngtcp2 が行うため、高レベル層から改めて送出しない (0240 で `reset_stream` の再利用を避けたのと同じ理由)
  - アプリ向けのコールバックは追加しない。層間 API の非対称として 0220 が扱う範囲であり、本 issue は nghttp3 への転送だけを対象とする
- **不採用**: `src/bindings/quic.cpp` の `NGTCP2_ERR_STREAM_SHUT_WR` 経路で QUIC 層から HTTP/3 層へ通知する案。層をまたぐ新しい通知経路が必要になり、ピアの STOP_SENDING 以外 (アプリ起点の終了) と区別できない。原因が既知である以上、原因側で扱う
- `on_stream_reset` の扱い (0240 の `shutdown_stream_read` の転送) は変えない。stop_sending と reset_stream は独立した方向を扱う
- 変更対象: `src/webtransport/http3/client.py` / `server.py`、`tests/`、`skills/webtransport-py/SKILL.md` (`http3.Client` / `http3.Server` の説明に転送の契約を追記する)

## 完了条件

- ピアが STOP_SENDING を送ると、DUT の低レベル `Http3Connection` で `shutdown_stream_write` が呼ばれ、以後そのストリームの `send_data` が no-op になる (送信バッファが作られない)
- 実 QUIC ピア (`quic.Client`) から低レベル `_connection.stop_sending(stream_id, error_code)` と `_send_pending()` で STOP_SENDING を送り、`http3.Server` と `http3.Client` の両側で転送を観測する (DUT の内部参照は既存 e2e テストの慣行に従う)
- 転送後に当該ストリームが `get_streams_to_send` に現れないこと (nghttp3 の書き込み側が閉じた証拠) を検証する
- 低レベル単体テスト (`http3.Connection` のクライアント・サーバーペア) で `shutdown_stream_write` の契約 (`shutdown_stream_write` 後の `send_data` が送信バッファを作らないこと、`ResetStream` などのイベントを push しないこと) を固定する。既存テストで未カバーなら追加する
- 各テストは高レベル層の転送を外すと失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- アプリ向けの `on_stop_sending` コールバックの追加 (0220 が扱う層間 API の非対称)
- ピアの RESET_STREAM の読み取り側の転送 (0240 で対応済み)
- STOP_SENDING を受けた後に同じストリームで送信を再開する API (RFC 9000 Section 3.5 により送信方向は終端し、再開できない)
- `src/webtransport/h3/` (WebTransport over HTTP/3) のピア STOP_SENDING の扱い。WebTransport のデータストリームは nghttp3 を介さない QUIC ストリームであり、転送先が異なる
