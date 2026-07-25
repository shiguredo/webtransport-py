"""webtransport.http3 高レベル API テスト"""

import asyncio

import pytest


def test_import_server_client():
    """Server と Client がインポートできることを確認"""
    from webtransport.http3 import Client, Server

    assert Server is not None
    assert Client is not None


def test_import_all():
    """全ての公開 API がインポートできることを確認"""
    from webtransport.http3 import (
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
    from webtransport.http3 import Server

    server = Server(host="127.0.0.1", port=0)
    assert server.host == "127.0.0.1"
    assert server.port == 0
    assert server.is_running is False


def test_server_init_with_options():
    """Server がオプション付きで初期化できることを確認"""
    from webtransport.http3 import Server

    server = Server(
        host="0.0.0.0",
        port=4433,
        idle_timeout_ns=60_000_000_000,
    )
    assert server.host == "0.0.0.0"
    assert server.port == 4433


def test_client_init():
    """Client が初期化できることを確認"""
    from webtransport.http3 import Client

    client = Client(host="example.com", port=443)
    assert client.host == "example.com"
    assert client.port == 443
    assert client.is_connected is False


def test_client_init_default_port():
    """Client がデフォルトポートで初期化できることを確認"""
    from webtransport.http3 import Client

    client = Client(host="example.com")
    assert client.host == "example.com"
    assert client.port == 443


def test_client_init_with_options():
    """Client がオプション付きで初期化できることを確認"""
    from webtransport.http3 import Client

    client = Client(
        host="example.com",
        port=8443,
        idle_timeout_ns=60_000_000_000,
    )
    assert client.host == "example.com"
    assert client.port == 8443


@pytest.mark.asyncio
async def test_server_start_stop():
    """Server の開始と停止ができることを確認"""
    from webtransport.http3 import Server

    server = Server(host="127.0.0.1", port=0)
    await server.start()
    assert server.is_running is True
    assert server.actual_port > 0

    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_context_manager():
    """Server がコンテキストマネージャーとして使えることを確認"""
    from webtransport.http3 import Server

    async with Server(host="127.0.0.1", port=0) as server:
        assert server.is_running is True
        assert server.actual_port > 0

    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_callbacks():
    """Server のコールバック設定ができることを確認"""
    from webtransport.http3 import Server

    server = Server(host="127.0.0.1", port=0)

    async def on_request(stream_id, headers, addr):
        pass

    async def on_data(stream_id, data, addr):
        pass

    server.on_request(on_request)
    server.on_data(on_data)

    assert server._on_request is not None
    assert server._on_data is not None


@pytest.mark.asyncio
async def test_client_callbacks():
    """Client のコールバック設定ができることを確認"""
    from webtransport.http3 import Client

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


@pytest.mark.asyncio
async def test_server_multiple_start_stop():
    """Server の複数回 start/stop ができることを確認"""
    from webtransport.http3 import Server

    server = Server(host="127.0.0.1", port=0)

    await server.start()
    assert server.is_running is True
    await server.stop()
    assert server.is_running is False

    await server.start()
    assert server.is_running is True
    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_client_communication(test_certificates):
    """Server と Client 間で HTTP/3 通信ができることを確認"""
    from webtransport.http3 import Client, Server

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

    async def on_request(stream_id, headers, addr):
        response_headers = [
            (":status", "200"),
            ("content-type", "text/plain"),
        ]
        await server.submit_response(addr, stream_id, response_headers)
        await server.send_data(addr, stream_id, b"Hello, HTTP/3!", fin=True)

    server.on_request(on_request)

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

    async def on_client_headers(stream_id, headers):
        received_headers.extend(headers)
        client_headers_received.set()

    async def on_client_data(stream_id, data):
        received_data.append(data)
        client_data_received.set()

    client.on_headers(on_client_headers)
    client.on_data(on_client_data)

    connected = await client.connect()
    assert connected is True

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
    assert received_data == [b"Hello, HTTP/3!"]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_client_post_with_body(test_certificates):
    """POST でリクエストボディを送り、サーバーがエコーして返すことを確認"""
    from webtransport.http3 import Client, Server

    server_received_bodies = []
    client_received_data = []
    server_body_received = asyncio.Event()
    client_data_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        # ヘッダー受信時点ではボディ待ち。応答は on_data で返す
        pass

    async def on_data(stream_id, data, addr):
        server_received_bodies.append(data)
        server_body_received.set()
        response_headers = [
            (":status", "200"),
            ("content-type", "application/octet-stream"),
        ]
        await server.submit_response(addr, stream_id, response_headers)
        await server.send_data(addr, stream_id, b"echo:" + data, fin=True)

    server.on_request(on_request)
    server.on_data(on_data)

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

    async def on_client_data(stream_id, data):
        client_received_data.append(data)
        client_data_received.set()

    client.on_data(on_client_data)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.request("POST", "/echo")
    assert stream_id >= 0
    await client.send_data(stream_id, b"payload", fin=True)

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
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_multiple_http3_requests(test_certificates):
    """同一接続で複数の GET リクエストが独立して処理されることを確認"""
    from webtransport.http3 import Client, Server

    # path -> レスポンスボディ
    client_responses = {}
    expected_paths = ["/a", "/b", "/c"]
    all_responses_received = asyncio.Event()
    # stream_id -> path (クライアント側)
    pending_paths = {}

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        path = "/"
        for name, value in headers:
            if name == ":path":
                path = value
                break
        response_headers = [
            (":status", "200"),
            ("content-type", "text/plain"),
        ]
        await server.submit_response(addr, stream_id, response_headers)
        await server.send_data(addr, stream_id, f"body-{path}".encode(), fin=True)

    server.on_request(on_request)

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

    async def on_client_data(stream_id, data):
        path = pending_paths.get(stream_id)
        if path is not None:
            client_responses[path] = data
        if len(client_responses) >= len(expected_paths):
            all_responses_received.set()

    client.on_data(on_client_data)

    connected = await client.connect()
    assert connected is True

    for path in expected_paths:
        stream_id = await client.request("GET", path)
        assert stream_id >= 0
        pending_paths[stream_id] = path

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(all_responses_received.wait(), timeout=5.0)

    for path in expected_paths:
        assert client_responses[path] == f"body-{path}".encode()

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_resets_http3_stream(test_certificates):
    """Server が HTTP/3 ストリームを reset すると Client に届くことを確認"""
    from webtransport.http3 import Client, Server

    client_reset_received = asyncio.Event()
    reset_info = {}

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        # リクエストを受けたら即座にリセットする
        await server.reset_stream(addr, stream_id, error_code=0x0101)

    server.on_request(on_request)

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

    async def on_client_stream_reset(stream_id, error_code):
        reset_info["stream_id"] = stream_id
        reset_info["error_code"] = error_code
        client_reset_received.set()

    client.on_stream_reset(on_client_stream_reset)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.request("GET", "/reset-me")
    assert stream_id >= 0

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(client_reset_received.wait(), timeout=5.0)

    assert reset_info["stream_id"] == stream_id
    assert reset_info["error_code"] == 0x0101

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()
