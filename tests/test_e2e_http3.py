"""webtransport.http3 高レベル API テスト"""

import asyncio
import socket
import time

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


@pytest.mark.asyncio
async def test_client_resets_http3_stream(test_certificates):
    """Client が HTTP/3 ストリームを reset すると Server に届くことを確認"""
    from webtransport.http3 import Client, Server

    server_reset_received = asyncio.Event()
    request_received = asyncio.Event()
    reset_info = {}

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        request_received.set()

    async def on_stream_reset(stream_id, error_code, addr):
        reset_info["stream_id"] = stream_id
        reset_info["error_code"] = error_code
        server_reset_received.set()

    server.on_request(on_request)
    server.on_stream_reset(on_stream_reset)

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

    stream_id = await client.request("GET", "/will-reset")
    assert stream_id >= 0

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(request_received.wait(), timeout=5.0)
    await client.reset_stream(stream_id, error_code=0x0102)
    await asyncio.wait_for(server_reset_received.wait(), timeout=5.0)

    assert reset_info["stream_id"] == stream_id
    assert reset_info["error_code"] == 0x0102

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_stream_end_callback(test_certificates):
    """レスポンス完了時に Client の on_stream_end が呼ばれることを確認"""
    from webtransport.http3 import Client, Server

    ended_stream_ids = []
    headers_received = asyncio.Event()
    data_received = asyncio.Event()
    stream_ended = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        await server.submit_response(addr, stream_id, [(":status", "200")])
        await server.send_data(addr, stream_id, b"done", fin=True)

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

    async def on_headers(stream_id, headers):
        headers_received.set()

    async def on_data(stream_id, data):
        data_received.set()

    async def on_stream_end(stream_id):
        ended_stream_ids.append(stream_id)
        stream_ended.set()

    client.on_headers(on_headers)
    client.on_data(on_data)
    client.on_stream_end(on_stream_end)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.request("GET", "/end")
    assert stream_id >= 0
    await client.send_data(stream_id, b"", fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(headers_received.wait(), timeout=5.0)
    await asyncio.wait_for(data_received.wait(), timeout=5.0)
    await asyncio.wait_for(stream_ended.wait(), timeout=5.0)

    assert ended_stream_ids == [stream_id]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_custom_request_headers(test_certificates):
    """追加リクエストヘッダーがサーバーに届くことを確認"""
    from webtransport.http3 import Client, Server

    server_headers = []
    headers_received = asyncio.Event()
    client_data = []
    client_data_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        server_headers.extend(headers)
        headers_received.set()
        await server.submit_response(addr, stream_id, [(":status", "200")])
        await server.send_data(addr, stream_id, b"ok", fin=True)

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
        client_data.append(data)
        client_data_received.set()

    client.on_data(on_client_data)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.request(
        "GET",
        "/headers",
        headers=[("x-trace-id", "abc-123"), ("x-custom", "value")],
    )
    assert stream_id >= 0

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(headers_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)

    header_map = dict(server_headers)
    assert header_map[":method"] == "GET"
    assert header_map[":path"] == "/headers"
    assert header_map["x-trace-id"] == "abc-123"
    assert header_map["x-custom"] == "value"
    assert client_data == [b"ok"]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_not_found_status(test_certificates):
    """サーバーが 404 を返したとき Client が :status を受け取れることを確認"""
    from webtransport.http3 import Client, Server

    received_status = []
    headers_received = asyncio.Event()
    data_received = asyncio.Event()
    body = []

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        await server.submit_response(
            addr,
            stream_id,
            [(":status", "404"), ("content-type", "text/plain")],
        )
        await server.send_data(addr, stream_id, b"not found", fin=True)

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

    async def on_headers(stream_id, headers):
        for name, value in headers:
            if name == ":status":
                received_status.append(value)
        headers_received.set()

    async def on_data(stream_id, data):
        body.append(data)
        data_received.set()

    client.on_headers(on_headers)
    client.on_data(on_data)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.request("GET", "/missing")
    assert stream_id >= 0

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(headers_received.wait(), timeout=5.0)
    await asyncio.wait_for(data_received.wait(), timeout=5.0)

    assert received_status == ["404"]
    assert body == [b"not found"]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_chunked_response_body(test_certificates):
    """サーバーがレスポンスボディを分割送信しても Client が結合できることを確認"""
    from webtransport.http3 import Client, Server

    client_chunks = []
    stream_ended = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        await server.submit_response(addr, stream_id, [(":status", "200")])
        await server.send_data(addr, stream_id, b"part-1-", fin=False)
        await server.send_data(addr, stream_id, b"part-2-", fin=False)
        await server.send_data(addr, stream_id, b"part-3", fin=True)

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

    async def on_data(stream_id, data):
        client_chunks.append(data)

    async def on_stream_end(stream_id):
        stream_ended.set()

    client.on_data(on_data)
    client.on_stream_end(on_stream_end)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.request("GET", "/chunked")
    assert stream_id >= 0
    await client.send_data(stream_id, b"", fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(stream_ended.wait(), timeout=5.0)

    assert b"".join(client_chunks) == b"part-1-part-2-part-3"

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_large_post_body(test_certificates):
    """大きな POST ボディがエコーされることを確認"""
    from webtransport.http3 import Client, Server

    payload = bytes((index % 256) for index in range(32 * 1024))
    server_buffer = bytearray()
    client_buffer = bytearray()
    server_complete = asyncio.Event()
    client_complete = asyncio.Event()
    response_sent = False

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        pass

    async def on_data(stream_id, data, addr):
        nonlocal response_sent
        server_buffer.extend(data)
        if not response_sent and len(server_buffer) >= len(payload):
            response_sent = True
            server_complete.set()
            await server.submit_response(
                addr,
                stream_id,
                [(":status", "200"), ("content-type", "application/octet-stream")],
            )
            await server.send_data(addr, stream_id, bytes(server_buffer), fin=True)

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
        client_buffer.extend(data)
        if len(client_buffer) >= len(payload):
            client_complete.set()

    client.on_data(on_client_data)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.request("POST", "/large")
    assert stream_id >= 0
    await client.send_data(stream_id, payload, fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(server_complete.wait(), timeout=10.0)
    await asyncio.wait_for(client_complete.wait(), timeout=10.0)

    assert bytes(server_buffer) == payload
    assert bytes(client_buffer) == payload

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_http3_client_run_exits_on_client_close(test_certificates):
    """Client.run() 実行中に close() が呼ばれると run() が終了することを回帰確認する

    close() は QUIC 側の close() を呼び CONNECTION_CLOSE をピアに送出する。
    Client.run() は次周回で QUIC の CONNECTION_CLOSED イベントを受けて
    _running = False になり終了する。新規追加した HTTP/3 層の is_closed()
    チェックが既存の QUIC 終了経路を壊していないことの回帰確認。
    """
    from webtransport.http3 import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
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

    # run() をバックグラウンドで起動、少し待ってから close を呼ぶ
    run_task = asyncio.create_task(client.run())
    await asyncio.sleep(0.05)

    await client.close()

    # QUIC CONNECTION_CLOSED 経路で数秒以内に終了する
    await asyncio.wait_for(run_task, timeout=3.0)
    assert run_task.done() is True

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await server.stop()


@pytest.mark.asyncio
async def test_http3_server_removes_client_on_client_close(test_certificates):
    """Client が close() したとき Server が該当 client を辞書から回収することを回帰確認する

    Client.close() で送出される QUIC CONNECTION_CLOSE を Server 側で受信し、
    既存 CONNECTION_CLOSED ハンドラで del self._clients[addr] される。
    新規追加した HTTP/3 層の is_closed() チェックが既存経路を
    壊していないことの回帰確認。
    """
    from webtransport.http3 import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
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

    # ハンドシェイクとクライアント登録を確定させるためリクエストを 1 本送る
    stream_id = await client.request("GET", "/")
    assert stream_id >= 0

    run_task = asyncio.create_task(client.run())
    await asyncio.sleep(0.2)

    # この時点で server._clients に addr が登録されている
    assert len(server._clients) == 1

    await client.close()

    # close() 経由で Client.run() が自然終了する (回帰: cancel 不要で終了する)
    await asyncio.wait_for(run_task, timeout=3.0)
    assert run_task.done() is True

    # server 側で該当 client が回収されていること
    # (public API 未提供のため private 属性で確認する)
    # client.close() で送出される CONNECTION_CLOSE の受信・処理はサーバー側の
    # イベントループ次第で、client.close() の完了と同期しないため、
    # 削除されるまで待ってから検証する (フレークを避ける)。CONNECTION_CLOSE
    # はクライアント送出済みで必ず届く (UDP ロスは not acked で ngtcp2 が再送)
    deadline = time.monotonic() + 5.0
    while len(server._clients) != 0 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert len(server._clients) == 0

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await server.stop()


@pytest.mark.asyncio
async def test_stream_end_callback_bodyless_response(test_certificates):
    """ボディなしレスポンスでも on_stream_end が 1 回だけ呼ばれることを確認

    204 レスポンス (ヘッダーのみ) で on_stream_end が 1 回だけ通知される
    ことを確認する。高レベル Server の現行送出構成ではヘッダーと FIN が
    別フレームになり得るが、実ブラウザ等が「ヘッダー + FIN を同一 QUIC
    STREAM_DATA で送る」正当なワイヤパターン (RFC 9114 Section 4.1 の
    メッセージフレーミングと Section 6 のフレーム境界の独立性) でも、
    on_stream_end は QUIC FIN の単一経路で通知されることをピン留めする。
    二重発火の抑制 (STREAM_END イベントを on_stream_end に使わない) の
    回帰ピン。
    """
    from webtransport.http3 import Client, Server

    ended_stream_ids = []
    stream_ended = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        # ボディなし (204) のレスポンスを送る
        await server.submit_response(addr, stream_id, [(":status", "204")])
        await server.send_data(addr, stream_id, b"", fin=True)

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

    async def on_stream_end(stream_id):
        ended_stream_ids.append(stream_id)
        stream_ended.set()

    client.on_stream_end(on_stream_end)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.request("GET", "/end")
    assert stream_id >= 0
    await client.send_data(stream_id, b"", fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(stream_ended.wait(), timeout=5.0)

    # 通知は 1 回だけ (二重発火しない)
    await asyncio.sleep(0.1)
    assert ended_stream_ids == [stream_id]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_run_continues_on_non_initial_packet(test_certificates):
    """未知アドレスからの非 Initial パケットで run() が継続することを確認

    接続クローズ済みアドレスからの追従パケット等、unknown アドレスからの
    非 Initial パケットは _accept_connection (quic.Connection.accept) が
    RuntimeError を投げる。サーバーは黙って破棄して run() を継続する
    (quic / h3 層の Server.run と同じ挙動)。未対策だとサーバータスクが
    例外終了する (遠隔 DoS の入口)。
    """
    from webtransport.http3 import Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    try:
        # 非 Initial パケット (長ヘッダーの 1 バイト目が 0x80 等でない、
        # accept のデコードで失敗するバイト列) を未知アドレスから送る
        loop = asyncio.get_running_loop()
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            await loop.sock_sendto(
                sender,
                b"\x00" + b"non-initial-packet" + (b"\xff" * 32),
                ("127.0.0.1", server.actual_port),
            )
        finally:
            sender.close()
        await asyncio.sleep(0.05)
        assert server_task.done() is False
        await asyncio.sleep(0.05)
        assert server_task.done() is False

        # 破棄後もサーバーが正常な接続を受け付けられることを確認する
        # (例外破棄の実装を残したままタイマー処理やループ制御が壊れた
        # 変更を検出するため)
        from webtransport.http3 import Client

        client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
        connected = await asyncio.wait_for(client.connect(), timeout=5.0)
        assert connected is True
        await client.close()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_server_stop_delivers_connection_close(test_certificates):
    """http3.Server.stop() が CONNECTION_CLOSE を送出してクライアントが終了を検知する

    修正前は stop() が close() を呼ぶだけで、生成された CONNECTION_CLOSE
    パケットを送出せずにソケットを閉じていた (クライアントは切断理由を
    受け取れず run() がタイムアウトまで待ち続ける)。修復後は quic / h3
    層の Server.stop() と同様に、close() 生成の CONNECTION_CLOSE を
    送出してからソケットを閉じる。クライアントの run() が受信した
    CONNECTION_CLOSE で自然終了することを検証する。
    """
    from webtransport.http3 import Client, Server

    client_finished_event = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    server_task = None
    client_task = None
    client = None
    try:
        await server.start()

        client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)

        async def run_server():
            try:
                await server.run()
            except asyncio.CancelledError:
                pass

        server_task = asyncio.create_task(run_server())

        connected = await asyncio.wait_for(client.connect(), timeout=5.0)
        assert connected is True

        async def run_client():
            try:
                await client.run()
                # run() が自然終了した (CancelledError ではない) 場合のみ
                # 到達する。stop() の CONNECTION_CLOSE を受信して run() が
                # 終了した証拠
                client_finished_event.set()
            except asyncio.CancelledError:
                pass

        client_task = asyncio.create_task(run_client())

        # サーバーを停止する。stop() は接続ごとに close() を呼び、生成された
        # CONNECTION_CLOSE をソケットから送出する
        await server.stop()
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)

        # クライアントは CONNECTION_CLOSE を受信して run() が自然終了する
        # (受信できなければタイムアウトで失敗する)
        await asyncio.wait_for(client_finished_event.wait(), timeout=5.0)
    finally:
        if client_task is not None:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
        if server_task is not None:
            server_task.cancel()
            await asyncio.gather(server_task, return_exceptions=True)
        if client is not None:
            await client.close()
        await server.stop()
