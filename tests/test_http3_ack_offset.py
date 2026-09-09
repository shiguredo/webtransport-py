"""HTTP/3 の ACK 通知による送信バッファ解放テスト

WebTransport over HTTP/3 側と対称の形式で検証する。
"""

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


def test_ack_offset_releases_send_buffer() -> None:
    """ACK 通知で送信バッファが解放されることを確認

    送信処理 (get_streams_to_send) で add_ack_offset が呼ばれ、
    acked_stream_data コールバック経由で stream_buffers_ から
    エントリが削除される
    """
    client, server = _create_connection_pair()

    # リクエストを送信してデータを積む
    assert client.submit_request(0, _request_headers()) is True
    client.send_data(0, b"hello", fin=True)

    # 送信前はバッファエントリが存在する
    assert client._has_stream_buffer(0) is True

    # 送信処理を実行すると ACK が通知され、バッファが解放される
    _pump(client, server)

    assert client._has_stream_buffer(0) is None


def test_ack_offset_releases_multiple_buffers() -> None:
    """複数のバッファエントリが ACK 通知で全て解放されることを確認"""
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    client.send_data(0, b"AAAA", fin=False)
    client.send_data(0, b"BBBB", fin=False)
    client.send_data(0, b"CCCC", fin=True)

    assert client._has_stream_buffer(0) is True

    _pump(client, server)

    assert client._has_stream_buffer(0) is None


def test_ack_offset_fin_only_releases_send_buffer() -> None:
    """FIN のみの送信 (データなし) でもバッファエントリが解放されることを確認

    fin=True でデータが空のエントリは read_data コールバックの空 FIN
    除去で削除される (データ量 0 のため ACK 経路では解放されない)
    """
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    client.send_data(0, b"", fin=True)

    assert client._has_stream_buffer(0) is True

    _pump(client, server)

    assert client._has_stream_buffer(0) is None


def test_ack_offset_bounds_entries_to_active_streams() -> None:
    """同時送信ストリーム数以下にエントリが収まることを確認

    複数ストリームの転送後も残留エントリが残らない
    """
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    client.send_data(0, b"first", fin=True)
    assert client.submit_request(4, _request_headers()) is True
    client.send_data(4, b"second", fin=True)

    assert client._has_stream_buffer(0) is True
    assert client._has_stream_buffer(4) is True

    _pump(client, server)

    assert client._has_stream_buffer(0) is None
    assert client._has_stream_buffer(4) is None


def test_ack_offset_repeated_round_trips_release_each_time() -> None:
    """繰り返し転送のたびに解放されることを確認

    同一ストリームへの送信と送出を繰り返しても残留が積み上がらない
    """
    client, server = _create_connection_pair()

    assert client.submit_request(0, _request_headers()) is True
    for index in range(5):
        client.send_data(0, f"chunk-{index}".encode(), fin=False)
        _pump(client, server)
        assert client._has_stream_buffer(0) is None
