"""webtransport.http2 高レベル API テスト"""

import asyncio

import pytest


def test_import_server_client():
    """Server と Client がインポートできることを確認"""
    from webtransport.http2 import Client, Server

    assert Server is not None
    assert Client is not None


def test_import_all():
    """全ての公開 API がインポートできることを確認"""
    from webtransport.http2 import (
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
    from webtransport.http2 import Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile="cert.pem",
        keyfile="key.pem",
    )
    assert server.host == "127.0.0.1"
    assert server.port == 0
    assert server.is_running is False


def test_client_init():
    """Client が初期化できることを確認"""
    from webtransport.http2 import Client

    client = Client(host="example.com", port=443)
    assert client.host == "example.com"
    assert client.port == 443
    assert client.is_connected is False


def test_client_init_default_port():
    """Client がデフォルトポートで初期化できることを確認"""
    from webtransport.http2 import Client

    client = Client(host="example.com")
    assert client.host == "example.com"
    assert client.port == 443


def test_server_callbacks():
    """Server のコールバック設定ができることを確認"""
    from webtransport.http2 import Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile="cert.pem",
        keyfile="key.pem",
    )

    async def on_request(stream_id, headers, response_writer):
        pass

    async def on_data(stream_id, data, response_writer):
        pass

    server.on_request(on_request)
    server.on_data(on_data)

    assert server._on_request is not None
    assert server._on_data is not None


def test_client_callbacks():
    """Client のコールバック設定ができることを確認"""
    from webtransport.http2 import Client

    client = Client(host="example.com", port=443)

    async def on_headers(stream_id, headers):
        pass

    async def on_data(stream_id, data):
        pass

    async def on_stream_end(stream_id):
        pass

    client.on_headers(on_headers)
    client.on_data(on_data)
    client.on_stream_end(on_stream_end)

    assert client._on_headers is not None
    assert client._on_data is not None
    assert client._on_stream_end is not None


def test_server_properties():
    """Server のプロパティが正しく設定されることを確認"""
    from webtransport.http2 import Server

    server = Server(
        host="0.0.0.0",
        port=8443,
        certfile="/path/to/cert.pem",
        keyfile="/path/to/key.pem",
    )
    assert server.host == "0.0.0.0"
    assert server.port == 8443
    assert server.actual_port == 0
    assert server.is_running is False


def test_client_properties():
    """Client のプロパティが正しく設定されることを確認"""
    from webtransport.http2 import Client

    client = Client(host="www.google.com", port=443)
    assert client.host == "www.google.com"
    assert client.port == 443
    assert client.is_connected is False


@pytest.mark.asyncio
async def test_server_start_stop(test_certificates):
    """Server の開始と停止ができることを確認"""
    from webtransport.http2 import Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()
    assert server.is_running is True
    assert server.actual_port > 0

    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_context_manager(test_certificates):
    """Server がコンテキストマネージャーとして使えることを確認"""
    from webtransport.http2 import Server

    async with Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    ) as server:
        assert server.is_running is True
        assert server.actual_port > 0

    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_client_communication(test_certificates):
    """Server と Client 間で HTTP/2 通信ができることを確認"""
    from webtransport.http2 import Client, Server

    received_headers = []
    received_data = []
    client_headers_received = asyncio.Event()
    client_data_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, response_writer):
        response_headers = [
            (":status", "200"),
            ("content-type", "text/plain"),
        ]
        await response_writer.send_headers(stream_id, response_headers)
        await response_writer.send_data(stream_id, b"Hello, HTTP/2!", end_stream=True)

    server.on_request(on_request)

    await server.start()

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    async def on_client_headers(stream_id, headers):
        received_headers.extend(headers)
        client_headers_received.set()

    async def on_client_data(stream_id, data):
        received_data.append(data)
        client_data_received.set()

    client.on_headers(on_client_headers)
    client.on_data(on_client_data)

    await client.connect()
    assert client.is_connected is True

    stream_id = await client.request("GET", "/")
    assert stream_id >= 0

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(client_headers_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)

    status_header = None
    for name, value in received_headers:
        if name == ":status":
            status_header = value
            break

    assert status_header == "200"
    assert received_data == [b"Hello, HTTP/2!"]

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_client_post_with_body(test_certificates):
    """POST のリクエストボディがサーバーに届き、エコーが返ることを確認

    Client.request に body を渡すとリクエストが終端され、サーバーの
    on_data でボディを受信できる。
    """
    from webtransport.http2 import Client, Server

    server_received_bodies: list[bytes] = []
    client_received_data: list[bytes] = []
    server_body_received = asyncio.Event()
    client_data_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, response_writer):
        # ヘッダー受信時点ではボディ待ち。応答は on_data で返す
        pass

    async def on_data(stream_id, data, response_writer):
        server_received_bodies.append(data)
        server_body_received.set()
        response_headers = [
            (":status", "200"),
            ("content-type", "application/octet-stream"),
        ]
        await response_writer.send_headers(stream_id, response_headers)
        await response_writer.send_data(stream_id, b"echo:" + data, end_stream=True)

    server.on_request(on_request)
    server.on_data(on_data)

    await server.start()

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    async def on_client_data(stream_id, data):
        client_received_data.append(data)
        client_data_received.set()

    client.on_data(on_client_data)

    await client.connect()
    assert client.is_connected is True

    stream_id = await client.request("POST", "/echo", body=b"payload")
    assert stream_id >= 0

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(server_body_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)

    assert server_received_bodies == [b"payload"]
    assert client_received_data == [b"echo:payload"]

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)

    await client.close()
    await server.stop()
