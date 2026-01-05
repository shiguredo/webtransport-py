"""WebTransport H3 デバッグテスト"""

import asyncio

import pytest


@pytest.mark.asyncio
async def test_debug_h3_low_level(test_certificates):
    """H3 低レベル API でセッション確立をテスト"""
    import socket

    from webtransport import h3 as h3_low, quic

    print("\n--- H3 Low Level Debug ---")

    server_quic_config = quic.Config()
    server_quic_config.alpn_protocols = ["h3"]
    server_quic_config.idle_timeout_ns = 30_000_000_000
    server_quic_config.cert_file = test_certificates["certfile"]
    server_quic_config.key_file = test_certificates["keyfile"]

    server_h3_config = h3_low.Config()
    server_h3_config.is_server = True

    client_quic_config = quic.Config()
    client_quic_config.alpn_protocols = ["h3"]
    client_quic_config.idle_timeout_ns = 30_000_000_000
    client_quic_config.verify_peer = False
    client_quic_config.server_name = "127.0.0.1"

    client_h3_config = h3_low.Config()
    client_h3_config.is_server = False

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.setblocking(False)
    server_socket.bind(("127.0.0.1", 0))
    server_port = server_socket.getsockname()[1]
    print(f"Server listening on port {server_port}")

    client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client_socket.setblocking(False)
    client_socket.bind(("0.0.0.0", 0))

    client_quic = quic.Connection.create_client(client_quic_config)
    client_h3 = h3_low.Session.create_client(client_h3_config)

    server_quic = None
    server_h3 = None
    server_streams_setup = False
    client_addr = None

    loop = asyncio.get_running_loop()

    async def exchange_data():
        nonlocal server_quic, server_h3, server_streams_setup

        server_data = server_quic.send() if server_quic else None
        if server_data:
            for stream_id, stream_data, fin in server_h3.get_streams_to_send() if server_h3 else []:
                server_quic.send_stream_data(stream_id, stream_data, fin)
            server_data = server_quic.send()
            if server_data:
                await loop.sock_sendto(
                    server_socket, server_data, ("127.0.0.1", client_socket.getsockname()[1])
                )

        client_data = client_quic.send()
        if client_data:
            await loop.sock_sendto(client_socket, client_data, ("127.0.0.1", server_port))

    initial_data = client_quic.send()
    if initial_data:
        print(f"Client sending initial ({len(initial_data)} bytes)")
        await loop.sock_sendto(client_socket, initial_data, ("127.0.0.1", server_port))

    handshake_complete = False
    session_ready = False
    client_control_setup = False
    connect_sent = False
    server_settings_received = False

    for i in range(200):
        try:
            data, addr = await asyncio.wait_for(
                loop.sock_recvfrom(server_socket, 65535),
                timeout=0.05,
            )
            if server_quic is None:
                server_quic = quic.Connection.accept(server_quic_config, data)
                server_quic.receive(data)
                server_h3 = h3_low.Session.create_server(server_h3_config)
                client_addr = addr
            else:
                server_quic.receive(data)
                client_addr = addr

            while True:
                event = server_quic.next_event()
                if event is None:
                    break
                if event.type == quic.EventType.HANDSHAKE_COMPLETED:
                    print("Server: QUIC HANDSHAKE_COMPLETED")
                    control_stream_id = server_quic.open_stream(False)
                    print(f"Server: control_stream_id={control_stream_id}")
                    server_h3.bind_control_stream(control_stream_id)
                    encoder_stream_id = server_quic.open_stream(False)
                    print(f"Server: encoder_stream_id={encoder_stream_id}")
                    server_h3.bind_qpack_encoder_stream(encoder_stream_id)
                    decoder_stream_id = server_quic.open_stream(False)
                    print(f"Server: decoder_stream_id={decoder_stream_id}")
                    server_h3.bind_qpack_decoder_stream(decoder_stream_id)
                    # クライアントからの双方向ストリームを受け入れる
                    server_h3.set_max_client_streams_bidi(100)
                    streams_to_send = server_h3.get_streams_to_send()
                    print(
                        f"Server: streams_to_send after control setup = {[(s[0], len(s[1]), s[2]) for s in streams_to_send]}"
                    )
                    for stream_id_send, stream_data, fin in streams_to_send:
                        server_quic.send_stream_data(stream_id_send, stream_data, fin)
                    server_data = server_quic.send()
                    if server_data:
                        print(f"Server: sending control streams ({len(server_data)} bytes)")
                        await loop.sock_sendto(server_socket, server_data, client_addr)
                    server_streams_setup = True
                elif event.type == quic.EventType.STREAM_DATA:
                    print(
                        f"Server: QUIC STREAM_DATA stream_id={event.stream_id} len={len(event.data)}"
                    )
                    server_h3.receive_stream_data(event.stream_id, event.data, event.fin)

            while True:
                h3_event = server_h3.next_event() if server_h3 else None
                if h3_event is None:
                    break
                print(f"Server: H3 event {h3_event.type}")
                if h3_event.type == h3_low.EventType.SESSION_READY:
                    print(f"Server: SESSION_READY session_id={h3_event.session_id}")
                    server_h3.accept_session(h3_event.session_id)
                    session_ready = True
                elif h3_event.type == h3_low.EventType.ERROR:
                    print(
                        f"Server: ERROR error_code={h3_event.error_code} error_message={h3_event.error_message}"
                    )

            for stream_id, stream_data, fin in server_h3.get_streams_to_send() if server_h3 else []:
                server_quic.send_stream_data(stream_id, stream_data, fin)
            server_data = server_quic.send() if server_quic else None
            if server_data:
                await loop.sock_sendto(server_socket, server_data, addr)
        except TimeoutError:
            pass

        try:
            data, _ = await asyncio.wait_for(
                loop.sock_recvfrom(client_socket, 65535),
                timeout=0.05,
            )
            client_quic.receive(data)

            while True:
                event = client_quic.next_event()
                if event is None:
                    break
                if event.type == quic.EventType.HANDSHAKE_COMPLETED:
                    print("Client: QUIC HANDSHAKE_COMPLETED")
                    handshake_complete = True
                    if not client_control_setup:
                        control_stream_id = client_quic.open_stream(False)
                        print(f"Client: control_stream_id={control_stream_id}")
                        client_h3.bind_control_stream(control_stream_id)
                        encoder_stream_id = client_quic.open_stream(False)
                        print(f"Client: encoder_stream_id={encoder_stream_id}")
                        client_h3.bind_qpack_encoder_stream(encoder_stream_id)
                        decoder_stream_id = client_quic.open_stream(False)
                        print(f"Client: decoder_stream_id={decoder_stream_id}")
                        client_h3.bind_qpack_decoder_stream(decoder_stream_id)
                        streams_to_send = client_h3.get_streams_to_send()
                        print(
                            f"Client: streams_to_send after control setup = {[(s[0], len(s[1]), s[2]) for s in streams_to_send]}"
                        )
                        for stream_id_send, stream_data, fin in streams_to_send:
                            client_quic.send_stream_data(stream_id_send, stream_data, fin)
                        client_data = client_quic.send()
                        if client_data:
                            print(f"Client: sending control streams ({len(client_data)} bytes)")
                            await loop.sock_sendto(
                                client_socket, client_data, ("127.0.0.1", server_port)
                            )
                        client_control_setup = True
                elif event.type == quic.EventType.STREAM_DATA:
                    print(
                        f"Client: QUIC STREAM_DATA stream_id={event.stream_id} len={len(event.data)}"
                    )
                    client_h3.receive_stream_data(event.stream_id, event.data, event.fin)
                    if event.stream_id == 3:
                        print(
                            "Client: received server control stream, server_settings_received=True"
                        )
                        server_settings_received = True

            while True:
                h3_event = client_h3.next_event()
                if h3_event is None:
                    break
                print(f"Client: H3 event {h3_event.type}")
                if h3_event.type == h3_low.EventType.SESSION_READY:
                    print(f"Client: SESSION_READY session_id={h3_event.session_id}")
                    session_ready = True

            if client_control_setup and server_settings_received and not connect_sent:
                request_stream_id = client_quic.open_stream(True)
                print(f"Client: request_stream_id={request_stream_id}")
                if client_h3.connect(
                    request_stream_id, f"https://127.0.0.1:{server_port}/webtransport"
                ):
                    print(f"Client: CONNECT sent on stream {request_stream_id}")
                    connect_sent = True

            for stream_id, stream_data, fin in client_h3.get_streams_to_send():
                client_quic.send_stream_data(stream_id, stream_data, fin)
            client_data = client_quic.send()
            if client_data:
                await loop.sock_sendto(client_socket, client_data, ("127.0.0.1", server_port))
        except TimeoutError:
            pass

        if session_ready:
            print("Session established!")
            break

        await asyncio.sleep(0.01)

    client_socket.close()
    server_socket.close()

    assert handshake_complete is True
    assert session_ready is True


@pytest.mark.asyncio
async def test_debug_quic_level(test_certificates):
    """QUIC レイヤーでの通信を確認"""
    from webtransport.quic import Client, Server

    server_handshake_done = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        alpn_protocols=["h3"],
    )

    async def on_handshake(addr):
        print(f"Server handshake completed with {addr}")
        server_handshake_done.set()

    server.on_handshake_completed(on_handshake)

    await server.start()
    print(f"\nServer started on port {server.actual_port}")

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        alpn_protocols=["h3"],
        verify_peer=False,
    )

    print("Client connecting...")
    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    print(f"Client connected: {connected}")

    await asyncio.wait_for(server_handshake_done.wait(), timeout=5.0)
    print("Server handshake confirmed")

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()

    assert connected is True
