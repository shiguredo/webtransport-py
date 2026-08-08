"""WebTransport over HTTP/3 の ACK 通知による送信バッファ解放テスト"""

from __future__ import annotations

from conftest import _establish_session, _pump


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
