"""HTTP/3 の送信側拡張 API テスト"""

from __future__ import annotations

from webtransport import http3


def _pump(src: http3.Connection, dst: http3.Connection) -> None:
    """src の送信データを全て dst に渡す

    QUIC レイヤーを介さず、get_streams_to_send で取り出したデータを
    receive_stream_data で直接渡す (モックなし)。get_streams_to_send は
    1 回の呼び出しで全てのデータを返すとは限らないため、データが無く
    なるまで繰り返す
    """
    for _ in range(64):
        sent = False
        for stream_id, data, fin in src.get_streams_to_send():
            dst.receive_stream_data(stream_id, data, fin)
            sent = True
        if not sent:
            break


def _create_connection_pair() -> tuple[http3.Connection, http3.Connection]:
    """Http3Connection のクライアント・サーバーペアを作成して初期化する

    @return (クライアント Connection, サーバー Connection)
    """
    client = http3.Connection.create_client(http3.Config())
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)

    # ストリームをバインド (クライアントの単方向ストリームは %4 == 2、
    # サーバーは %4 == 3)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)

    # 双方の SETTINGS を交換する
    _pump(server, client)
    _pump(client, server)

    return client, server


def _request_headers() -> list[tuple[str, str]]:
    """テスト用のリクエストヘッダー"""
    return [
        (":method", "GET"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]


def test_http3_submit_trailers() -> None:
    """トレーラが本体の後に送信されることを確認"""
    client, server = _create_connection_pair()

    # リクエストヘッダーを先に送信処理する
    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)

    # 本体を fin=True で積み、flush 前にトレーラを呼ぶ
    client.send_data(0, b"request-body", fin=True)
    assert client.submit_trailers(0, [("x-trailer", "trailer-value")]) is True
    _pump(client, server)

    # リクエストヘッダー → 本体 → トレーラ → ストリーム終端の順に届く
    event = server.next_event()
    assert event is not None
    assert event.type == http3.EventType.HEADERS
    assert dict(event.headers)[":method"] == "GET"

    event = server.next_event()
    assert event is not None
    assert event.type == http3.EventType.DATA
    assert event.data == b"request-body"

    # トレーラも本体ヘッダーと同じ HEADERS イベントとして積まれる
    event = server.next_event()
    assert event is not None
    assert event.type == http3.EventType.HEADERS
    assert dict(event.headers)["x-trailer"] == "trailer-value"

    event = server.next_event()
    assert event is not None
    assert event.type == http3.EventType.STREAM_END
    assert server.next_event() is None


def test_http3_submit_trailers_after_fin_fails() -> None:
    """send_data(fin=True) の送信処理後に submit_trailers が失敗することを確認

    送信処理で read_data_cb が EOF を返すと WRITE_END_STREAM が立ち、
    トレーラは NGHTTP3_ERR_INVALID_STATE になる
    """
    client, _server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    _pump(client, _server)

    # 本体を送信処理すると WRITE_END_STREAM が立つ
    client.send_data(0, b"request-body", fin=True)
    _pump(client, _server)
    assert client.submit_trailers(0, [("x-trailer", "trailer-value")]) is False


def test_http3_submit_info() -> None:
    """1xx レスポンスが最終レスポンスより先に送信されることを確認"""
    client, server = _create_connection_pair()

    # リクエストを送信する
    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)
    assert server.next_event() is not None

    # 1xx を送ってから最終レスポンスを送る (103 Early Hints)
    assert server.submit_info(0, [(":status", "103")]) is True
    assert server.submit_response(0, [(":status", "200")]) is True
    # nghttp3 はフレームキュー (HEADERS) を DATA より先に書き出す
    _pump(server, client)
    server.send_data(0, b"response-body", fin=True)
    _pump(server, client)

    # 1xx → 最終レスポンス → 本体 → ストリーム終端の順に届く
    event = client.next_event()
    assert event is not None
    assert event.type == http3.EventType.HEADERS
    assert dict(event.headers)[":status"] == "103"

    event = client.next_event()
    assert event is not None
    assert event.type == http3.EventType.HEADERS
    assert dict(event.headers)[":status"] == "200"

    event = client.next_event()
    assert event is not None
    assert event.type == http3.EventType.DATA
    assert event.data == b"response-body"

    # DATA フレームで終わるストリームの STREAM_END は QUIC 層の
    # ストリーム終了通知 (close_stream) で生成される
    client.close_stream(0, 0)
    event = client.next_event()
    assert event is not None
    assert event.type == http3.EventType.STREAM_END
    assert client.next_event() is None


def test_http3_submit_info_client_guard() -> None:
    """クライアントの submit_info が失敗することを確認

    nghttp3 は conn->server を assert するため、C++ 側のガードで
    クライアントの呼び出しを拒否する
    """
    client, _server = _create_connection_pair()
    assert client.submit_info(0, [(":status", "103")]) is False


def test_http3_submit_shutdown_notice() -> None:
    """shutdown notice が GOAWAY として届くことを確認"""
    client, server = _create_connection_pair()

    assert server.submit_shutdown_notice() is True
    # GOAWAY_QUEUED のみで SHUTDOWN_COMMENCED は立たないため、
    # 送信処理後もドレイン状態にはならない
    assert server.drained is False
    _pump(server, client)
    assert server.drained is False

    # サーバーの shutdown notice は最大ストリーム ID 版 GOAWAY として届く
    # (NGHTTP3_SHUTDOWN_NOTICE_STREAM_ID = (1 << 62) - 4)
    event = client.next_event()
    assert event is not None
    assert event.type == http3.EventType.GO_AWAY
    assert event.push_id == (1 << 62) - 4
    assert client.next_event() is None


def test_http3_submit_shutdown_notice_client_guard() -> None:
    """クライアントの submit_shutdown_notice が失敗することを確認

    shutdown notice はサーバーの graceful shutdown の開始通知であり、
    クライアントでは PUSH ID 版 GOAWAY になるため C++ 側のガードで
    拒否する
    """
    client, _server = _create_connection_pair()
    assert client.submit_shutdown_notice() is False


def test_http3_submit_shutdown_notice_then_goaway() -> None:
    """notice → goaway の正順で GOAWAY ID が単調減少することを確認

    RFC 9114 5.2 節の MUST NOT に従い、shutdown notice の GOAWAY ID
    ((1 << 62) - 4) の後に shutdown の GOAWAY ID (0) が届く
    """
    client, server = _create_connection_pair()

    assert server.submit_shutdown_notice() is True
    _pump(server, client)
    event = client.next_event()
    assert event is not None
    assert event.type == http3.EventType.GO_AWAY
    assert event.push_id == (1 << 62) - 4

    # 通知後に graceful shutdown を完了させる。このテストはリクエストを
    # 一切送っていないため、サーバーの GOAWAY ID は max_stream_id_bidi
    # 初期値 -4 + 4 = 0 になる (RFC 9114 5.2 節の "MAY be zero if no
    # requests or pushes were processed")
    server.goaway(0)
    _pump(server, client)
    event = client.next_event()
    assert event is not None
    assert event.type == http3.EventType.GO_AWAY
    assert event.push_id == 0
    assert client.next_event() is None

    # GOAWAY の書き出しが完了するとドレイン状態になる
    assert server.drained is True


def test_http3_submit_shutdown_notice_after_goaway_fails() -> None:
    """goaway() 後の submit_shutdown_notice が失敗することを確認

    shutdown notice の GOAWAY ID は shutdown の GOAWAY ID より大きいため、
    GOAWAY ID の単調減少 (RFC 9114 5.2 節の MUST NOT) に違反する
    """
    _client, server = _create_connection_pair()

    server.goaway(0)
    assert server.submit_shutdown_notice() is False


def test_http3_submit_shutdown_notice_twice_fails() -> None:
    """submit_shutdown_notice の多重呼び出しが失敗することを確認

    同一 GOAWAY ID の重複送信を避けるため、2 回目以降は False を返す
    """
    _client, server = _create_connection_pair()

    assert server.submit_shutdown_notice() is True
    assert server.submit_shutdown_notice() is False


def test_http3_submit_shutdown_notice_control_stream_unbound() -> None:
    """制御ストリーム未バインドの submit_shutdown_notice が失敗することを確認

    nghttp3 は tx.ctrl が設定されていることを assert するため、
    C++ 側のガードで未バインドの呼び出しを拒否する
    """
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)
    assert server.submit_shutdown_notice() is False


def test_http3_shutdown_stream_write() -> None:
    """書き込み側シャットダウン後の send_data が no-op になることを確認"""
    client, server = _create_connection_pair()

    # リクエストヘッダーを先に送信処理する
    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)
    assert server.next_event() is not None

    # 本体を送信処理する
    client.send_data(0, b"request-body")
    _pump(client, server)
    event = server.next_event()
    assert event is not None
    assert event.type == http3.EventType.DATA
    assert event.data == b"request-body"

    # 書き込み側をシャットダウンすると書き込み不可になる
    client.shutdown_stream_write(0)
    assert client.stream_writable(0) == 0

    # シャットダウン後の send_data は no-op となり送出されない
    client.send_data(0, b"more-data")
    _pump(client, server)
    assert server.next_event() is None


def test_http3_submit_trailers_after_shutdown_stream_write_fails() -> None:
    """書き込み側シャットダウン後の submit_trailers が失敗することを確認"""
    client, _server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    _pump(client, _server)

    client.shutdown_stream_write(0)
    assert client.submit_trailers(0, [("x-trailer", "trailer-value")]) is False


def test_http3_submit_trailers_qpack_unbound() -> None:
    """QPACK ストリーム未バインドの submit_trailers が失敗することを確認

    nghttp3 は tx.qenc が設定されていることを assert するため、
    C++ 側のガードで未バインドの呼び出しを拒否する
    """
    client = http3.Connection.create_client(http3.Config())
    assert client.submit_trailers(0, [("x-trailer", "trailer-value")]) is False


def test_http3_submit_info_qpack_unbound() -> None:
    """QPACK ストリーム未バインドの submit_info が失敗することを確認

    nghttp3 は tx.qenc が設定されていることを assert するため、
    C++ 側のガードで未バインドの呼び出しを拒否する
    """
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)
    assert server.submit_info(0, [(":status", "103")]) is False


def test_http3_shutdown_stream_write_blocks_pending() -> None:
    """書き込み側シャットダウン後に未送信データが送出されないことを確認

    nghttp3 は SHUT_WR フラグを立て、クライアント発双方向ストリーム
    (%4 == 0) ではスケジューラからも外すため、シャットダウン前に積んだ
    データも送信されない
    """
    client, server = _create_connection_pair()

    # リクエストヘッダーを先に送信処理する
    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)
    assert server.next_event() is not None

    # 送信処理前に書き込み側をシャットダウンする
    client.send_data(0, b"request-body")
    client.shutdown_stream_write(0)
    _pump(client, server)

    # 本体は送出されない
    assert server.next_event() is None


def test_http3_headers_fin_same_chunk_stream_end_once() -> None:
    """ヘッダーと FIN が同一チャンクで届いたときに STREAM_END が 1 回であることを確認

    レスポンス送信側が「ヘッダー + FIN」を 1 回の送信にまとめた場合
    (RFC 9114 Section 4.1 のメッセージフレーミングと Section 6 のフレーム
    境界と QUIC STREAM_DATA 境界の独立性。1 チャンクで届くのは正当な
    ワイヤパターン)、受信側の低レベルは
    end_headers_cb の fin=1 で STREAM_END イベントを 1 回積む
    (ヘッダー終端の終端検知)。イベントが重複しないことをピンする。
    高レベル層がこのイベントを on_stream_end に使わないこと (QUIC FIN
    の単一経路化) は client.py の run() の実装で担保される。
    """
    client, server = _create_connection_pair()

    # クライアントがリクエストを送信し、サーバーが受理する
    assert client.submit_request(0, _request_headers()) is True
    _pump(client, server)

    # サーバーが 200 応答 + FIN を 1 回の get_streams_to_send で書き出す
    # (送信側はヘッダーと DATA (空) をまとめて flush し、1 チャンクに収まる)
    assert server.submit_response(0, [(":status", "200")]) is True
    server.send_data(0, b"", fin=True)
    # 1 回目の取り出しでヘッダー + FIN を含むチャンクが返る
    streams = server.get_streams_to_send()
    assert any(stream_id == 0 and fin for stream_id, data, fin in streams)
    for stream_id, data, fin in streams:
        client.receive_stream_data(stream_id, data, fin)

    # ヘッダー + FIN の同一チャンクでも STREAM_END は 1 回のみ
    events = []
    while True:
        event = client.next_event()
        if event is None:
            break
        events.append(event)
    assert any(event.type == http3.EventType.HEADERS for event in events)
    end_events = [event for event in events if event.type == http3.EventType.STREAM_END]
    assert len(end_events) == 1
