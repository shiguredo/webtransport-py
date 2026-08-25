"""QUIC の過大データグラムの破棄テスト

RFC 9221 Section 3 の「An endpoint MUST NOT send DATAGRAM frames that are
larger than the max_datagram_frame_size value it has received from its peer」
に従い、ピアの上限を超えるデータグラムを破棄し、後続のデータグラムの
送出がブロックされないことを検証する。
"""

from __future__ import annotations

from conftest import (
    CERTFILE,
    CLIENT_ADDR,
    KEYFILE,
    SERVER_ADDR,
    perform_handshake,
)

from webtransport.quic import Config, Connection, EventType


def _create_handshaken_pair(
    server_max_datagram_frame_size: int,
) -> tuple[Connection, Connection]:
    """サーバー側の max_datagram_frame_size を指定したハンドシェイク済みペアを作る

    サーバーの transport parameter はクライアントの送信上限となる (RFC 9221
    Section 3)。
    """
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]
    server_config.max_datagram_frame_size = server_max_datagram_frame_size

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    initial_packet = client.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    assert server is not None

    assert perform_handshake(client, server, initial_packet.data) is True
    return client, server


def _deliver_datagram(client: Connection, server: Connection) -> list[bytes]:
    """クライアントの送信パケットを全てサーバーへ渡し、受信結果を返す

    QUIC の ACK 往復 (クライアント → サーバー → サーバーからの ACK) を
    繰り返し、サーバー側の DatagramReceived イベントを集める。
    """
    received: list[bytes] = []
    for _ in range(64):
        sent = False
        packet = client.send()
        if packet:
            server.receive(packet.data, SERVER_ADDR, CLIENT_ADDR)
            sent = True
        # サーバーからの ACK をクライアントに返す
        ack = server.send()
        if ack:
            client.receive(ack.data, CLIENT_ADDR, SERVER_ADDR)
        # サーバー側の受信イベント (DatagramReceived) を集める
        while True:
            event = server.next_event()
            if event is None:
                break
            if event.type == EventType.DATAGRAM:
                received.append(event.data)
        if not sent:
            break
    return received


def test_oversized_datagram_dropped_after_small_one_delivered() -> None:
    """過大データグラムが破棄され、後続のデータグラムが送出されることを確認

    サーバーが最大 100 バイトのデータグラムを広告したセッションで、
    クライアントに「200 バイトの過大データグラム → 小さいデータグラム」の
    順で送信した場合、過大なものは破棄され、後続の小さいデータグラムは
    サーバーへ届く (修正前は過大データグラムがキュー先頭に残り続け、
    後続の全データグラムが送出されなかった)。
    """
    client, server = _create_handshaken_pair(100)

    # 過大 (フレーム全体で 100 超) なデータグラムを送信する
    client.send_datagram(b"x" * 200)

    # その後の小さいデータグラムは送出され、サーバーへ届く
    client.send_datagram(b"ok")
    received = _deliver_datagram(client, server)
    assert received == [b"ok"]


def test_oversized_datagram_at_write_time_dropped_after_handshake() -> None:
    """ハンドシェイク前にキューされた過大データグラムが書き出し時に破棄されることを確認

    ハンドシェイク前 (transport parameter 受信前) はエンキュー時の検査が
    できないため、過大なデータグラムをキューしても検出できない。ハンド
    シェイク完了後の書き出し時にキュー先頭の過大データグラムを破棄して、
    後続のデータグラムが送出されることを確認する (server 側の広告を
    100 バイトにした構成)。
    """
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]
    server_config.max_datagram_frame_size = 100

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)

    # ハンドシェイク前 (TP 未受信) に過大なデータグラムをキューする
    client.send_datagram(b"y" * 200)
    client.send_datagram(b"ok")

    initial_packet = client.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    assert server is not None
    assert perform_handshake(client, server, initial_packet.data) is True

    # 書き出し時に過大なものは破棄され、後続が届く
    received = _deliver_datagram(client, server)
    assert received == [b"ok"]


def test_oversized_datagram_varint_length_boundary() -> None:
    """varint 長の切れ目 (63/64 バイト) で破棄判定が一貫することを確認

    データ長 63 バイトは varint 長 1 (フレーム長 = 1 + 1 + 63 = 65)、64
    バイトは varint 長 2 (フレーム長 = 1 + 2 + 64 = 67)。上限 66 バイトの
    セッションでは 63 バイト (65 <= 66) は送出され、64 バイト (67 > 66) は
    破棄される。エンキュー時検査の varint 長計算が ngtcp2 の
    ngtcp2_pkt_datagram_framelen と一致することをピンする。
    """
    client, server = _create_handshaken_pair(66)

    client.send_datagram(b"z" * 63)
    received = _deliver_datagram(client, server)
    assert received == [b"z" * 63]

    client.send_datagram(b"a" * 64)
    client.send_datagram(b"ok")
    received = _deliver_datagram(client, server)
    assert received == [b"ok"]
