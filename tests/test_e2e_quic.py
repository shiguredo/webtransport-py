"""webtransport.quic 高レベル API テスト"""

import asyncio

import pytest


def test_import_server_client():
    """Server と Client がインポートできることを確認"""
    from webtransport.quic import Client, Server

    assert Server is not None
    assert Client is not None


def test_import_all():
    """全ての公開 API がインポートできることを確認"""
    from webtransport.quic import (
        Client,
        Config,
        Connection,
        Event,
        EventType,
        Server,
        get_version,
    )

    assert Server is not None
    assert Client is not None
    assert Config is not None
    assert Connection is not None
    assert Event is not None
    assert EventType is not None
    assert get_version is not None


def test_server_init():
    """Server が初期化できることを確認"""
    from webtransport.quic import Server

    server = Server(host="127.0.0.1", port=0)
    assert server.host == "127.0.0.1"
    assert server.port == 0
    assert server.is_running is False


def test_server_init_with_options():
    """Server がオプション付きで初期化できることを確認"""
    from webtransport.quic import Server

    server = Server(
        host="0.0.0.0",
        port=4433,
        alpn_protocols=["h3", "hq-interop"],
        idle_timeout_ns=60_000_000_000,
    )
    assert server.host == "0.0.0.0"
    assert server.port == 4433


def test_client_init():
    """Client が初期化できることを確認"""
    from webtransport.quic import Client

    client = Client(host="localhost", port=4433)
    assert client.host == "localhost"
    assert client.port == 4433
    assert client.is_connected is False


def test_client_init_with_options():
    """Client がオプション付きで初期化できることを確認"""
    from webtransport.quic import Client

    client = Client(
        host="example.com",
        port=443,
        alpn_protocols=["h3"],
        idle_timeout_ns=60_000_000_000,
    )
    assert client.host == "example.com"
    assert client.port == 443


@pytest.mark.asyncio
async def test_server_start_stop():
    """Server の開始と停止ができることを確認"""
    from webtransport.quic import Server

    server = Server(host="127.0.0.1", port=0)
    await server.start()
    assert server.is_running is True
    assert server.actual_port > 0

    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_context_manager():
    """Server がコンテキストマネージャーとして使えることを確認"""
    from webtransport.quic import Server

    async with Server(host="127.0.0.1", port=0) as server:
        assert server.is_running is True
        assert server.actual_port > 0

    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_callbacks():
    """Server のコールバック設定ができることを確認"""
    from webtransport.quic import Server

    server = Server(host="127.0.0.1", port=0)

    callback_called = {"handshake": False, "stream": False, "datagram": False, "closed": False}

    async def on_handshake(addr):
        callback_called["handshake"] = True

    async def on_stream_data(stream_id, data, fin, addr):
        callback_called["stream"] = True

    async def on_datagram(data, addr):
        callback_called["datagram"] = True

    async def on_closed(addr):
        callback_called["closed"] = True

    server.on_handshake_completed(on_handshake)
    server.on_stream_data(on_stream_data)
    server.on_datagram(on_datagram)
    server.on_connection_closed(on_closed)

    assert server._on_handshake_completed is not None
    assert server._on_stream_data is not None
    assert server._on_datagram is not None
    assert server._on_connection_closed is not None


@pytest.mark.asyncio
async def test_client_callbacks():
    """Client のコールバック設定ができることを確認"""
    from webtransport.quic import Client

    client = Client(host="localhost", port=4433)

    async def on_handshake():
        pass

    async def on_stream_data(stream_id, data, fin):
        pass

    async def on_datagram(data):
        pass

    async def on_closed():
        pass

    client.on_handshake_completed(on_handshake)
    client.on_stream_data(on_stream_data)
    client.on_datagram(on_datagram)
    client.on_connection_closed(on_closed)

    assert client._on_handshake_completed is not None
    assert client._on_stream_data is not None
    assert client._on_datagram is not None
    assert client._on_connection_closed is not None


@pytest.mark.asyncio
async def test_server_multiple_start_stop():
    """Server の複数回 start/stop ができることを確認"""
    from webtransport.quic import Server

    server = Server(host="127.0.0.1", port=0)

    await server.start()
    port1 = server.actual_port
    assert server.is_running is True
    await server.stop()
    assert server.is_running is False

    await server.start()
    port2 = server.actual_port
    assert server.is_running is True
    assert port2 > 0
    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_client_stream_communication(test_certificates):
    """Server と Client 間でストリーム通信ができることを確認"""
    from webtransport.quic import Client, Server

    received_data = []
    server_received_data = []
    handshake_completed = asyncio.Event()
    client_data_received = asyncio.Event()
    server_data_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_handshake(addr):
        handshake_completed.set()

    async def on_server_stream_data(stream_id, data, fin, addr):
        server_received_data.append(data)
        server_data_received.set()
        await server.send_stream_data(addr, stream_id, b"pong", fin=True)

    server.on_handshake_completed(on_handshake)
    server.on_stream_data(on_server_stream_data)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    async def on_client_stream_data(stream_id, data, fin):
        received_data.append(data)
        client_data_received.set()

    client.on_stream_data(on_client_stream_data)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"ping", fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(server_data_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)

    assert server_received_data == [b"ping"]
    assert received_data == [b"pong"]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_initiated_stream(test_certificates):
    """Server からストリームを開いて通信ができることを確認"""
    from webtransport.quic import Client, Server

    client_received_data = []
    server_received_data = []
    handshake_completed = asyncio.Event()
    client_data_received = asyncio.Event()
    server_data_received = asyncio.Event()
    server_addr = None

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_handshake(addr):
        nonlocal server_addr
        server_addr = addr
        handshake_completed.set()

    async def on_server_stream_data(stream_id, data, fin, addr):
        server_received_data.append(data)
        server_data_received.set()

    server.on_handshake_completed(on_handshake)
    server.on_stream_data(on_server_stream_data)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    async def on_client_stream_data(stream_id, data, fin):
        client_received_data.append(data)
        if fin:
            client_data_received.set()
            await client.send_stream_data(stream_id, b"client-response", fin=True)

    client.on_stream_data(on_client_stream_data)

    connected = await client.connect()
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(handshake_completed.wait(), timeout=5.0)

    stream_id = await server.open_stream(server_addr)
    assert stream_id >= 0

    await server.send_stream_data(server_addr, stream_id, b"server-initiated", fin=True)

    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)
    await asyncio.wait_for(server_data_received.wait(), timeout=5.0)

    assert client_received_data == [b"server-initiated"]
    assert server_received_data == [b"client-response"]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_stream_with_fin_flag(test_certificates):
    """FIN フラグ付きストリームが正しく処理されることを確認"""
    from webtransport.quic import Client, Server

    server_received = []
    client_received = []
    server_data_received = asyncio.Event()
    client_data_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        server_received.append((data, fin))
        if fin:
            server_data_received.set()
            await server.send_stream_data(addr, stream_id, b"response", fin=True)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    async def on_client_stream_data(stream_id, data, fin):
        client_received.append((data, fin))
        if fin:
            client_data_received.set()

    client.on_stream_data(on_client_stream_data)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.open_stream()
    # FIN フラグ付きでデータ送信
    await client.send_stream_data(stream_id, b"final-data", fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(server_data_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)

    # FIN フラグが正しく伝達されていることを確認
    assert len(server_received) >= 1
    assert server_received[-1] == (b"final-data", True)
    assert len(client_received) >= 1
    assert client_received[-1] == (b"response", True)

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_multiple_streams_with_more_flag(test_certificates):
    """複数ストリーム送信で MORE フラグが正しく機能することを確認"""
    from webtransport.quic import Client, Server

    server_received = {}
    all_streams_received = asyncio.Event()
    expected_streams = 3

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        if stream_id not in server_received:
            server_received[stream_id] = []
        server_received[stream_id].append((data, fin))
        if len(server_received) >= expected_streams:
            all_complete = all(
                any(fin for _, fin in msgs) for msgs in server_received.values()
            )
            if all_complete:
                all_streams_received.set()

    server.on_stream_data(on_server_stream_data)
    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    connected = await client.connect()
    assert connected is True

    # 複数のストリームを開いてデータを送信
    # MORE フラグにより効率的にパケットが合体される
    stream_ids = []
    for i in range(expected_streams):
        stream_id = await client.open_stream()
        stream_ids.append(stream_id)

    # 各ストリームにデータを送信
    for i, stream_id in enumerate(stream_ids):
        await client.send_stream_data(stream_id, f"stream-{i}-data".encode(), fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(all_streams_received.wait(), timeout=5.0)

    # 全てのストリームデータが受信されたことを確認
    assert len(server_received) == expected_streams
    for i, stream_id in enumerate(stream_ids):
        assert stream_id in server_received
        messages = server_received[stream_id]
        assert any(
            data == f"stream-{i}-data".encode() and fin
            for data, fin in messages
        )

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_stream_and_datagram_combined(test_certificates):
    """ストリームとデータグラムの同時送信が正しく機能することを確認"""
    from webtransport.quic import Client, Server

    server_stream_data = []
    server_datagrams = []
    stream_received = asyncio.Event()
    datagram_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        server_stream_data.append((stream_id, data, fin))
        if fin:
            stream_received.set()

    async def on_server_datagram(data, addr):
        server_datagrams.append(data)
        datagram_received.set()

    server.on_stream_data(on_server_stream_data)
    server.on_datagram(on_server_datagram)
    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    connected = await client.connect()
    assert connected is True

    # ストリームとデータグラムを同時に送信
    # MORE フラグによりストリームとデータグラムが効率的に処理される
    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"stream-data", fin=True)
    await client.send_datagram(b"datagram-data")

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(stream_received.wait(), timeout=5.0)
    await asyncio.wait_for(datagram_received.wait(), timeout=5.0)

    # 両方のデータが正しく受信されたことを確認
    assert len(server_stream_data) >= 1
    assert server_stream_data[-1][1] == b"stream-data"
    assert server_stream_data[-1][2] is True
    assert b"datagram-data" in server_datagrams

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_client_datagram_communication(test_certificates):
    """Server と Client 間でデータグラム通信ができることを確認"""
    from webtransport.quic import Client, Server

    client_received_datagrams = []
    server_received_datagrams = []
    client_datagram_received = asyncio.Event()
    server_datagram_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_datagram(data, addr):
        server_received_datagrams.append(data)
        server_datagram_received.set()
        await server.send_datagram(addr, b"datagram-pong")

    server.on_datagram(on_server_datagram)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    async def on_client_datagram(data):
        client_received_datagrams.append(data)
        client_datagram_received.set()

    client.on_datagram(on_client_datagram)

    connected = await client.connect()
    assert connected is True

    await client.send_datagram(b"datagram-ping")

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(server_datagram_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_datagram_received.wait(), timeout=5.0)

    assert server_received_datagrams == [b"datagram-ping"]
    assert client_received_datagrams == [b"datagram-pong"]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()
