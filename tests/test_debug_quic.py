"""QUIC デバッグテスト"""


def test_quic_client_create():
    """QUIC Client の作成のみをテスト"""
    from webtransport import quic

    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"
    config.verify_peer = False

    conn = quic.Connection.create_client(config)
    assert conn is not None

    assert conn.is_closed() is False
    assert conn.is_handshake_completed() is False
    print("Client created successfully")


def test_quic_client_send():
    """QUIC Client の send() を呼び出してみる"""
    from webtransport import quic

    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"
    config.verify_peer = False

    conn = quic.Connection.create_client(config)
    assert conn is not None
    print("Calling send()...")

    result = conn.send()
    print(f"send result: {result}")
    if result is not None:
        print(f"send result length: {len(result)}")


def test_quic_server_create(test_certificates):
    """QUIC Server の作成をテスト"""
    from webtransport import quic

    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.cert_file = test_certificates["certfile"]
    config.key_file = test_certificates["keyfile"]

    conn = quic.Connection.create_server(config)
    assert conn is not None

    assert conn.is_closed() is False
    assert conn.is_handshake_completed() is False
    print("Server connection created successfully")


def test_quic_accept(test_certificates):
    """QUIC accept をテスト"""
    from webtransport import quic

    client_config = quic.Config()
    client_config.alpn_protocols = ["h3"]
    client_config.server_name = "localhost"
    client_config.verify_peer = False

    client = quic.Connection.create_client(client_config)
    assert client is not None
    print("Client created")

    initial_packet = client.send()
    assert initial_packet is not None
    print(f"Initial packet: {len(initial_packet)} bytes")
    print(f"Initial packet header (first 30 bytes): {initial_packet[:30].hex()}")

    server_config = quic.Config()
    server_config.alpn_protocols = ["h3"]
    server_config.cert_file = test_certificates["certfile"]
    server_config.key_file = test_certificates["keyfile"]

    server = quic.Connection.accept(server_config, initial_packet)
    assert server is not None
    print("Server accepted connection")

    recv_result = server.receive(initial_packet)
    print(f"Server receive initial result: {recv_result}")

    print(f"Client connection ID: {client.get_connection_id().hex()}")
    print(f"Server connection ID: {server.get_connection_id().hex()}")

    def process_events(name, conn):
        """イベントを処理する"""
        while True:
            event = conn.next_event()
            if event is None:
                break
            print(f"  {name} event: {event.type}")
            if event.type == quic.EventType.CONNECTION_CLOSED:
                print(f"    error_code={event.error_code}, reason={event.reason}")

    for i in range(20):
        server_data = server.send()
        if server_data:
            print(f"[{i}] Server -> Client: {len(server_data)} bytes")
            if len(server_data) < 100:
                print(f"    Data (hex): {server_data.hex()}")
            recv_result = client.receive(server_data)
            print(f"    Client receive result: {recv_result}")
            process_events("Client", client)

        client_data = client.send()
        if client_data:
            print(f"[{i}] Client -> Server: {len(client_data)} bytes")
            server.receive(client_data)
            process_events("Server", server)

        if client.is_handshake_completed() and server.is_handshake_completed():
            print(f"Handshake completed after {i} iterations")
            break

        if not server_data and not client_data:
            print(f"No more data to exchange at iteration {i}")
            break

    print(f"Client handshake completed: {client.is_handshake_completed()}")
    print(f"Server handshake completed: {server.is_handshake_completed()}")
    print(f"Client closed: {client.is_closed()}")
    print(f"Server closed: {server.is_closed()}")
