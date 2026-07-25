"""webtransport.h2 (WebTransport over HTTP/2) 高レベル API テスト"""

import asyncio

import pytest


def test_import_server_client():
    """Server と Client がインポートできることを確認"""
    from webtransport.h2 import Client, Server

    assert Server is not None
    assert Client is not None


def test_import_all():
    """全ての公開 API がインポートできることを確認"""
    from webtransport.h2 import (
        Client,
        Config,
        Event,
        EventType,
        Server,
        Session,
    )

    assert Server is not None
    assert Client is not None
    assert Config is not None
    assert Event is not None
    assert EventType is not None
    assert Session is not None


def test_server_init():
    """Server が初期化できることを確認"""
    from webtransport.h2 import Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile="cert.pem",
        keyfile="key.pem",
    )
    assert server.host == "127.0.0.1"
    assert server.port == 0
    assert server.actual_port == 0
    assert server.is_running is False


def test_server_init_with_different_host():
    """Server が異なるホストで初期化できることを確認"""
    from webtransport.h2 import Server

    server = Server(
        host="0.0.0.0",
        port=8443,
        certfile="/path/to/cert.pem",
        keyfile="/path/to/key.pem",
    )
    assert server.host == "0.0.0.0"
    assert server.port == 8443


def test_client_init():
    """Client が初期化できることを確認"""
    from webtransport.h2 import Client

    client = Client(url="https://localhost:8443/webtransport")
    assert client.url == "https://localhost:8443/webtransport"
    assert client.host == "localhost"
    assert client.port == 8443
    assert client.is_connected is False
    assert client.session_id == -1


def test_client_init_url_parse():
    """Client が URL を正しくパースできることを確認"""
    from webtransport.h2 import Client

    client = Client(url="https://example.com:443/wt/session")
    assert client.host == "example.com"
    assert client.port == 443


def test_client_init_url_default_port():
    """Client がデフォルトポートで URL をパースできることを確認"""
    from webtransport.h2 import Client

    client = Client(url="https://example.com/webtransport")
    assert client.host == "example.com"
    assert client.port == 443


def test_client_init_url_no_path():
    """Client がパスなし URL をパースできることを確認"""
    from webtransport.h2 import Client

    client = Client(url="https://example.com:8443")
    assert client.host == "example.com"
    assert client.port == 8443


def test_server_callbacks():
    """Server のコールバック設定ができることを確認"""
    from webtransport.h2 import Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile="cert.pem",
        keyfile="key.pem",
    )

    async def on_session_ready(session_writer):
        pass

    async def on_session_closed(session_writer):
        pass

    async def on_stream_data(stream_id, data, session_writer):
        pass

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)
    server.on_stream_data(on_stream_data)

    assert server._on_session_ready is not None
    assert server._on_session_closed is not None
    assert server._on_stream_data is not None


def test_client_callbacks():
    """Client のコールバック設定ができることを確認"""
    from webtransport.h2 import Client

    client = Client(url="https://localhost:8443/webtransport")

    async def on_session_ready(session_id):
        pass

    async def on_session_closed(session_id):
        pass

    async def on_stream_data(stream_id, data):
        pass

    client.on_session_ready(on_session_ready)
    client.on_session_closed(on_session_closed)
    client.on_stream_data(on_stream_data)

    assert client._on_session_ready is not None
    assert client._on_session_closed is not None
    assert client._on_stream_data is not None


def test_client_properties():
    """Client のプロパティが正しく設定されることを確認"""
    from webtransport.h2 import Client

    client = Client(url="https://www.example.com:9443/wt")
    assert client.url == "https://www.example.com:9443/wt"
    assert client.host == "www.example.com"
    assert client.port == 9443
    assert client.is_connected is False
    assert client.session_id == -1


def test_config_properties():
    """Config のプロパティが正しく設定できることを確認"""
    from webtransport.h2 import Config

    config = Config()
    config.is_server = True
    assert config.is_server is True

    config.is_server = False
    assert config.is_server is False


def test_event_type_values():
    """EventType の値が定義されていることを確認"""
    from webtransport.h2 import EventType

    assert hasattr(EventType, "SESSION_READY")
    assert hasattr(EventType, "SESSION_CLOSED")
    assert hasattr(EventType, "STREAM_DATA")
    assert hasattr(EventType, "STREAM_RESET")
    assert hasattr(EventType, "DATAGRAM")
    assert hasattr(EventType, "SESSION_DRAINING")


def test_client_datagram_and_reset_callbacks():
    """Client の datagram / reset コールバック設定ができることを確認"""
    from webtransport.h2 import Client

    client = Client(url="https://localhost:8443/webtransport")

    async def on_datagram(data: bytes) -> None:
        pass

    async def on_stream_reset(stream_id: int, error_code: int) -> None:
        pass

    client.on_datagram(on_datagram)
    client.on_stream_reset(on_stream_reset)

    assert client._on_datagram is not None
    assert client._on_stream_reset is not None


def test_server_datagram_and_reset_callbacks():
    """Server の datagram / reset コールバック設定ができることを確認"""
    from webtransport.h2 import Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile="cert.pem",
        keyfile="key.pem",
    )

    async def on_datagram(data, session_writer) -> None:
        pass

    async def on_stream_reset(stream_id, error_code, session_writer) -> None:
        pass

    server.on_datagram(on_datagram)
    server.on_stream_reset(on_stream_reset)

    assert server._on_datagram is not None
    assert server._on_stream_reset is not None


def test_session_create_server():
    """Session がサーバーモードで作成できることを確認"""
    from webtransport.h2 import Config, Session

    config = Config()
    config.is_server = True
    session = Session.create_server(config)
    assert session is not None


def test_session_create_client():
    """Session がクライアントモードで作成できることを確認"""
    from webtransport.h2 import Config, Session

    config = Config()
    config.is_server = False
    session = Session.create_client(config)
    assert session is not None


@pytest.mark.asyncio
async def test_server_start_stop(test_certificates):
    """Server の開始と停止ができることを確認"""
    from webtransport.h2 import Server

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
    from webtransport.h2 import Server

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
    """Server と Client 間で WebTransport 通信ができることを確認"""
    from webtransport.h2 import Client, Server

    client_received_data = []
    server_received_data = []
    session_ready_event = asyncio.Event()
    client_data_received = asyncio.Event()
    server_data_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_writer):
        session_ready_event.set()

    async def on_stream_data(stream_id, data, session_writer):
        server_received_data.append(data)
        server_data_received.set()
        await session_writer.send_stream_data(stream_id, b"pong", fin=False)

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_client_stream_data(stream_id, data):
        client_received_data.append(data)
        client_data_received.set()

    client.on_stream_data(on_client_stream_data)

    connected = await client.connect()
    assert connected is True

    # WebTransport ストリームを開く (Capsule Protocol を使用)
    stream_id = await client.open_stream(unidirectional=False)
    assert stream_id >= 0

    await client.send_stream_data(stream_id, b"ping", fin=False)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
    await asyncio.wait_for(server_data_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)

    assert server_received_data == [b"ping"]
    assert client_received_data == [b"pong"]

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_client_datagram_communication(test_certificates):
    """DATAGRAM capsule で双方向通信できることを確認"""
    from webtransport.h2 import Client, Server

    client_received: list[bytes] = []
    server_received: list[bytes] = []
    client_dg = asyncio.Event()
    server_dg = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_datagram(data: bytes, session_writer) -> None:
        server_received.append(data)
        server_dg.set()
        await session_writer.send_datagram(b"pong-dg")

    server.on_datagram(on_datagram)
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_client_datagram(data: bytes) -> None:
        client_received.append(data)
        client_dg.set()

    client.on_datagram(on_client_datagram)

    assert await client.connect() is True

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    await client.send_datagram(b"ping-dg")

    await asyncio.wait_for(server_dg.wait(), timeout=5.0)
    await asyncio.wait_for(client_dg.wait(), timeout=5.0)

    assert server_received == [b"ping-dg"]
    assert client_received == [b"pong-dg"]

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_unidirectional_stream(test_certificates):
    """単方向ストリームでデータを送れることを確認"""
    from webtransport.h2 import Client, Server

    server_received: list[bytes] = []
    server_data = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_stream_data(stream_id: int, data: bytes, session_writer) -> None:
        server_received.append(data)
        if data:
            server_data.set()

    server.on_stream_data(on_stream_data)
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    assert await client.connect() is True

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    stream_id = await client.open_stream(unidirectional=True)
    assert stream_id >= 0
    await client.send_stream_data(stream_id, b"uni-hello", fin=True)

    await asyncio.wait_for(server_data.wait(), timeout=5.0)
    assert b"uni-hello" in server_received

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_session_close_notifies_server(test_certificates):
    """クライアントの close_session がサーバーに通知されることを確認"""
    from webtransport.h2 import Client, Server

    session_closed = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_closed(session_writer) -> None:
        session_closed.set()

    server.on_session_closed(on_session_closed)
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    assert await client.connect() is True

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    await asyncio.sleep(0.1)
    await client.close()

    await asyncio.wait_for(session_closed.wait(), timeout=5.0)

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)
    await server.stop()


@pytest.mark.asyncio
async def test_server_resets_client_stream(test_certificates):
    """サーバーがクライアントストリームをリセットできることを確認"""
    from webtransport.h2 import Client, Server

    reset_event = asyncio.Event()
    reset_codes: list[int] = []

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_stream_data(stream_id: int, data: bytes, session_writer) -> None:
        if data:
            await session_writer.reset_stream(stream_id, error_code=42)

    server.on_stream_data(on_stream_data)
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_stream_reset(stream_id: int, error_code: int) -> None:
        reset_codes.append(error_code)
        reset_event.set()

    client.on_stream_reset(on_stream_reset)
    assert await client.connect() is True

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    stream_id = await client.open_stream(unidirectional=False)
    await client.send_stream_data(stream_id, b"reset-me", fin=False)

    await asyncio.wait_for(reset_event.wait(), timeout=5.0)
    assert 42 in reset_codes

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_chunked_stream_data(test_certificates):
    """複数回に分けたストリームデータが届くことを確認"""
    from webtransport.h2 import Client, Server

    server_chunks: list[bytes] = []
    done = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_stream_data(stream_id: int, data: bytes, session_writer) -> None:
        if data:
            server_chunks.append(data)
        if b"".join(server_chunks) == b"ABCDEF":
            done.set()

    server.on_stream_data(on_stream_data)
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    assert await client.connect() is True

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    stream_id = await client.open_stream(unidirectional=False)
    await client.send_stream_data(stream_id, b"ABC", fin=False)
    await client.send_stream_data(stream_id, b"DEF", fin=True)

    await asyncio.wait_for(done.wait(), timeout=5.0)
    assert b"".join(server_chunks) == b"ABCDEF"

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_is_webtransport_ready_after_settings(test_certificates):
    """SETTINGS 交換後に is_webtransport_ready が真になることを確認"""
    from webtransport.h2 import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    assert await client.connect() is True
    assert client._session is not None
    assert client._session.is_webtransport_ready() is True

    await client.close()
    await server.stop()
