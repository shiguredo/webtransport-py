"""QUIC エラー処理のテスト

ngtcp2 API のエラー処理が正しく動作することを確認するテスト
"""

from conftest import (
    CLIENT_ADDR,
    SERVER_ADDR,
    create_client_server_pair,
    perform_handshake,
)


def test_close_nonexistent_stream():
    """存在しないストリームをクローズしてもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 存在しないストリーム ID でクローズを試みる
    nonexistent_stream_id = 9999
    client.close_stream(nonexistent_stream_id, 0)

    # 接続は閉じられていないこと
    assert not client.is_closed()


def test_send_data_to_closed_stream():
    """クローズしたストリームにデータを送信"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    stream_id = client.open_stream(True)
    assert stream_id >= 0

    # ストリームをクローズ
    client.close_stream(stream_id, 0)

    # クローズしたストリームにデータを送信
    client.send_stream_data(stream_id, b"test data", False)

    # send() を呼んでもクラッシュしない
    client.send()

    # 接続は閉じられていないこと
    assert not client.is_closed()


def test_connection_draining_state():
    """接続がドレイン状態になった場合のエラー処理"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # サーバーから接続をクローズ
    # close() は CONNECTION_CLOSE パケットを生成して closed_ フラグを立てる
    server.close(0, "normal close")

    # close() 後は closed_ が true になり、send() が CONNECTION_CLOSE を返す
    assert server.is_closed()

    # クライアント側では、サーバーからの明示的なクローズパケットがなくても
    # タイムアウトで接続が閉じられる
    # ここでは close() が正しく closed_ フラグを設定することを確認
    assert not client.is_closed()  # クライアントはまだ閉じていない


def test_receive_after_close():
    """接続クローズ後にパケットを受信してもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # サーバーがパケットを生成
    server_packet = server.send()

    # クライアントが接続をクローズ
    client.close(0, "client close")

    # クローズ後にパケットを受信
    if server_packet:
        result = client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
        # クラッシュしないことを確認
        assert result == 0  # クローズ後は 0 バイト処理


def test_send_after_close():
    """接続クローズ後は CONNECTION_CLOSE を 1 回だけ送出し、以降は None を返す"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 接続をクローズ
    client.close(0, "normal close")

    # close() 後は closed_ が true
    assert client.is_closed()

    # close() 後に send() を呼ぶと CONNECTION_CLOSE パケットを 1 回だけ返す
    result = client.send()
    assert result is not None

    # 2 回目以降の send() は None を返す
    result = client.send()
    assert result is None


def test_stream_data_after_fin():
    """FIN 送信後にデータを送信"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    stream_id = client.open_stream(True)
    assert stream_id >= 0

    # FIN フラグ付きでデータを送信
    client.send_stream_data(stream_id, b"final data", True)

    # パケットを送信
    packet = client.send()
    if packet:
        server.receive(packet.data, SERVER_ADDR, CLIENT_ADDR)

    # FIN 後にさらにデータを送信しようとする
    client.send_stream_data(stream_id, b"more data", False)

    # send() を呼んでもクラッシュしない
    client.send()

    # 接続は閉じられていないこと
    assert not client.is_closed()


def test_multiple_close_calls():
    """接続を複数回クローズしても CONNECTION_CLOSE は 1 回だけ送出される"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 複数回クローズを呼ぶ (2 回目以降は closed_ ガードで no-op)
    client.close(0, "first close")
    client.close(1, "second close")
    client.close(2, "third close")

    # クラッシュしないこと、closed_ が立つことを確認
    assert client.is_closed()

    # CONNECTION_CLOSE は 1 回だけ送出される (2 回目以降は None)
    result = client.send()
    assert result is not None
    assert client.send() is None


def test_multiple_stream_close_calls():
    """同じストリームを複数回クローズしてもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    stream_id = client.open_stream(True)
    assert stream_id >= 0

    # 同じストリームを複数回クローズ
    client.close_stream(stream_id, 0)
    client.close_stream(stream_id, 1)
    client.close_stream(stream_id, 2)

    # 接続は閉じられていないこと
    assert not client.is_closed()


def test_handle_timeout_without_activity():
    """アクティビティなしでタイムアウト処理を呼んでもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # タイムアウト処理を複数回呼ぶ
    for _ in range(10):
        client.handle_timeout()
        server.handle_timeout()

    # 接続は閉じられていないこと
    assert not client.is_closed()
    assert not server.is_closed()


def test_open_stream_after_close():
    """接続クローズ後にストリームを開こうとしても -1 が返る"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 接続をクローズ
    client.close(0, "normal close")

    # クローズ後にストリームを開こうとする
    stream_id = client.open_stream(True)
    assert stream_id == -1


def test_datagram_after_close():
    """接続クローズ後にデータグラムを送信してもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 接続をクローズ
    client.close(0, "normal close")

    # クローズ後にデータグラムを送信
    client.send_datagram(b"test datagram")

    # send() を呼んでもクラッシュしない。close() が生成した
    # CONNECTION_CLOSE パケットが返る
    result = client.send()
    assert result is not None
