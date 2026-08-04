"""WebTransport over HTTP/3 の ACK 通知による送信バッファ解放テスト"""

from __future__ import annotations

from webtransport import h3


def _pump(src: h3.Session, dst: h3.Session) -> None:
    """src の送信データを dst に渡す

    QUIC レイヤーを介さず、get_streams_to_send で取り出したデータを
    receive_stream_data で直接渡す (モックなし)
    """
    for stream_id, data, fin in src.get_streams_to_send():
        dst.receive_stream_data(stream_id, data, fin)


def _establish_session() -> tuple[h3.Session, h3.Session, int]:
    """h3.Session 同士で WebTransport セッションを確立する

    @return (クライアント Session, サーバー Session, セッション ID)
    """
    client = h3.Session.create_client(h3.Config())
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)

    # ストリームをバインド (クライアントの単方向ストリームは %4 == 2、
    # サーバーは %4 == 3)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)
    server.set_max_client_streams_bidi(100)

    # サーバーの SETTINGS をクライアントに送る
    _pump(server, client)

    # クライアントが CONNECT を送信
    assert client.connect(0, "https://localhost/webtransport") is True
    _pump(client, server)

    # サーバーがセッションを受理
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_READY:
            assert server.accept_session(event.session_id) is True
    _pump(server, client)

    # クライアント側の SESSION_READY を確認
    session_id: int | None = None
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_READY:
            session_id = event.session_id
    assert session_id is not None

    return client, server, session_id


def test_ack_offset_releases_send_buffer() -> None:
    """ACK 通知で送信バッファが解放されることを確認

    送信処理 (get_streams_to_send) で add_ack_offset が呼ばれ、
    acked_stream_data コールバック経由で stream_buffers_ から
    エントリが削除される
    """
    client, _server, session_id = _establish_session()

    # データストリームを開いてデータを送信
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"hello", fin=True)

    # 送信前はバッファエントリが存在する
    assert client._has_stream_buffer(stream_id) is True

    # 送信処理を実行すると ACK が通知され、バッファが解放される
    _pump(client, _server)

    assert client._has_stream_buffer(stream_id) is None


def test_ack_offset_releases_multiple_buffers() -> None:
    """複数のバッファエントリが ACK 通知で全て解放されることを確認"""
    client, _server, session_id = _establish_session()

    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"AAAA", fin=False)
    client.send_stream_data(stream_id, b"BBBB", fin=False)
    client.send_stream_data(stream_id, b"CCCC", fin=True)

    assert client._has_stream_buffer(stream_id) is True

    _pump(client, _server)

    assert client._has_stream_buffer(stream_id) is None


def test_ack_offset_fin_only_releases_send_buffer() -> None:
    """FIN のみの送信 (データなし) でもバッファエントリが解放されることを確認

    fin=True でデータが空のエントリは書き出し時にデータ量 0 が通知され、
    エントリが空になるため削除される
    """
    client, _server, session_id = _establish_session()

    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"", fin=True)

    assert client._has_stream_buffer(stream_id) is True

    _pump(client, _server)

    assert client._has_stream_buffer(stream_id) is None
