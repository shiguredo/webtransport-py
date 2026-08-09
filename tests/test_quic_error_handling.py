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
    """接続クローズ後にパケットを受信すると CONNECTION_CLOSE が再アームされる"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # サーバーがパケットを生成
    server_packet = server.send()
    assert server_packet is not None

    # クライアントが接続をクローズ
    client.close(0, "client close")

    # 初回の CONNECTION_CLOSE を取り出す
    close_packet = client.send()
    assert close_packet is not None
    assert client.send() is None

    # クローズ後にパケットを受信
    result = client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
    # close() で CONNECTION_CLOSE を生成できた場合は受信処理が走り、
    # NGTCP2_ERR_CLOSING で 0 が返る (クラッシュしない)
    assert result == 0

    # 受信パケットへの応答として、初回と同じ CONNECTION_CLOSE が再アームされ
    # て返る (RFC 9000 Section 10.2.1 の同一パケット再送)
    retransmitted = client.send()
    assert retransmitted is not None
    assert retransmitted.data == close_packet.data


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


def test_connection_close_retransmission_on_receive():
    """close() 後の受信パケットに応答して CONNECTION_CLOSE を再送する"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # ハンドシェイク完了イベントを消費しておく (close() 後に新たなイベントが
    # 積まれないことを検証するため)
    while client.next_event() is not None:
        pass
    while server.next_event() is not None:
        pass

    # サーバーが close() して CONNECTION_CLOSE を生成・保持する
    server.close(0x100, "server error")
    assert server.is_closed()

    # 初回の CONNECTION_CLOSE を取り出すが、ピアには渡さない (UDP ロスを再現)
    close_packet = server.send()
    assert close_packet is not None

    # 受信を挟まない 2 回目の send() は None (初回配送の契約維持)
    assert server.send() is None

    # ピア (クライアント) は接続が生きていると思い、ストリームにデータを積む
    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, b"hello", False)
    client_packet = client.send()
    assert client_packet is not None

    # サーバーがそのパケットを受信すると CONNECTION_CLOSE が再アームされる
    result = server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
    assert result == 0

    # 受信パケットへの応答として、初回と同じ CONNECTION_CLOSE が再送される
    # (RFC 9000 Section 10.2.1 の同一パケット再送)
    retransmitted = server.send()
    assert retransmitted is not None
    assert retransmitted.data == close_packet.data

    # 再送後は再び受信を挟まない限り None に戻る (受信データグラムごとに 1 回)
    assert server.send() is None

    # 2 回目の受信でも同じパケットが再送される (再アームの繰り返し)
    client.send_stream_data(stream_id, b"world", False)
    client_packet = client.send()
    assert client_packet is not None
    assert server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR) == 0
    retransmitted_again = server.send()
    assert retransmitted_again is not None
    assert retransmitted_again.data == close_packet.data

    # アプリが自ら close() を呼んだため、再アーム経路では終了イベントは
    # push されない (close() 自体もイベントを push しない)
    assert server.next_event() is None


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
