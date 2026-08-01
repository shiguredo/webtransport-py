"""webtransport.h3 (WebTransport over HTTP/3) 高レベル API テスト"""

import asyncio

import pytest


def test_import_server_client():
    """Server と Client がインポートできることを確認"""
    from webtransport.h3 import Client, Server

    assert Server is not None
    assert Client is not None


def test_import_all():
    """全ての公開 API がインポートできることを確認"""
    from webtransport.h3 import (
        Client,
        Config,
        Event,
        EventType,
        Server,
        Session,
        StreamInfo,
    )

    assert Server is not None
    assert Client is not None
    assert Config is not None
    assert Event is not None
    assert EventType is not None
    assert Session is not None
    assert StreamInfo is not None


def test_server_init():
    """Server が初期化できることを確認"""
    from webtransport.h3 import Server

    server = Server(host="127.0.0.1", port=0)
    assert server.host == "127.0.0.1"
    assert server.port == 0
    assert server.actual_port == 0
    assert server.is_running is False


def test_server_init_with_options():
    """Server がオプション付きで初期化できることを確認"""
    from webtransport.h3 import Server

    server = Server(
        host="0.0.0.0",
        port=4433,
        idle_timeout_ns=60_000_000_000,
    )
    assert server.host == "0.0.0.0"
    assert server.port == 4433


def test_client_init():
    """Client が初期化できることを確認"""
    from webtransport.h3 import Client

    client = Client(url="https://localhost:4433/webtransport")
    assert client.url == "https://localhost:4433/webtransport"
    assert client.host == "localhost"
    assert client.port == 4433
    assert client.is_connected is False
    assert client.session_id == -1


def test_client_init_url_parse():
    """Client が URL を正しくパースできることを確認"""
    from webtransport.h3 import Client

    client = Client(url="https://example.com:443/wt/session")
    assert client.host == "example.com"
    assert client.port == 443


def test_client_init_url_default_port():
    """Client がデフォルトポートで URL をパースできることを確認"""
    from webtransport.h3 import Client

    client = Client(url="https://example.com/webtransport")
    assert client.host == "example.com"
    assert client.port == 443


def test_client_init_url_no_path():
    """Client がパスなし URL をパースできることを確認"""
    from webtransport.h3 import Client

    client = Client(url="https://example.com:8443")
    assert client.host == "example.com"
    assert client.port == 8443


def test_client_init_with_options():
    """Client がオプション付きで初期化できることを確認"""
    from webtransport.h3 import Client

    client = Client(
        url="https://example.com:8443/wt",
        idle_timeout_ns=60_000_000_000,
    )
    assert client.host == "example.com"
    assert client.port == 8443


@pytest.mark.asyncio
async def test_server_start_stop():
    """Server の開始と停止ができることを確認"""
    from webtransport.h3 import Server

    server = Server(host="127.0.0.1", port=0)
    await server.start()
    assert server.is_running is True
    assert server.actual_port > 0

    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_context_manager():
    """Server がコンテキストマネージャーとして使えることを確認"""
    from webtransport.h3 import Server

    async with Server(host="127.0.0.1", port=0) as server:
        assert server.is_running is True
        assert server.actual_port > 0

    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_multiple_start_stop():
    """Server の複数回 start/stop ができることを確認"""
    from webtransport.h3 import Server

    server = Server(host="127.0.0.1", port=0)

    await server.start()
    assert server.is_running is True
    await server.stop()
    assert server.is_running is False

    await server.start()
    assert server.is_running is True
    await server.stop()
    assert server.is_running is False


def test_server_callbacks():
    """Server のコールバック設定ができることを確認"""
    from webtransport.h3 import Server

    server = Server(host="127.0.0.1", port=0)

    async def on_session_ready(session_id, addr):
        pass

    async def on_session_closed(session_id, addr):
        pass

    async def on_stream_data(session_id, stream_id, data, addr):
        pass

    async def on_datagram(session_id, data, addr):
        pass

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)
    server.on_stream_data(on_stream_data)
    server.on_datagram(on_datagram)

    assert server._on_session_ready is not None
    assert server._on_session_closed is not None
    assert server._on_stream_data is not None
    assert server._on_datagram is not None


def test_client_callbacks():
    """Client のコールバック設定ができることを確認"""
    from webtransport.h3 import Client

    client = Client(url="https://localhost:4433/webtransport")

    async def on_session_ready(session_id):
        pass

    async def on_session_closed(session_id):
        pass

    async def on_stream_data(stream_id, data):
        pass

    async def on_datagram(data):
        pass

    client.on_session_ready(on_session_ready)
    client.on_session_closed(on_session_closed)
    client.on_stream_data(on_stream_data)
    client.on_datagram(on_datagram)

    assert client._on_session_ready is not None
    assert client._on_session_closed is not None
    assert client._on_stream_data is not None
    assert client._on_datagram is not None


def test_client_properties():
    """Client のプロパティが正しく設定されることを確認"""
    from webtransport.h3 import Client

    client = Client(url="https://www.example.com:9443/wt")
    assert client.url == "https://www.example.com:9443/wt"
    assert client.host == "www.example.com"
    assert client.port == 9443
    assert client.is_connected is False
    assert client.session_id == -1


def test_config_properties():
    """Config のプロパティが正しく設定できることを確認"""
    from webtransport.h3 import Config

    config = Config()
    config.is_server = True
    assert config.is_server is True

    config.is_server = False
    assert config.is_server is False


def test_event_type_values():
    """EventType の値が定義されていることを確認"""
    from webtransport.h3 import EventType

    assert hasattr(EventType, "SESSION_READY")
    assert hasattr(EventType, "SESSION_CLOSED")
    assert hasattr(EventType, "STREAM_DATA")
    assert hasattr(EventType, "STREAM_CLOSED")
    assert hasattr(EventType, "RESET_STREAM")
    assert hasattr(EventType, "STOP_SENDING")
    assert hasattr(EventType, "DATAGRAM")


def test_session_create_server():
    """Session がサーバーモードで作成できることを確認"""
    from webtransport.h3 import Config, Session

    config = Config()
    config.is_server = True
    session = Session.create_server(config)
    assert session is not None


def test_session_create_client():
    """Session がクライアントモードで作成できることを確認"""
    from webtransport.h3 import Config, Session

    config = Config()
    config.is_server = False
    session = Session.create_client(config)
    assert session is not None


@pytest.mark.asyncio
async def test_client_connect_with_origin(test_certificates):
    """origin を指定して接続が確立できることを確認する

    サーバーは Origin ヘッダーを無視して受理するため、このテストが検証する
    のは「Client コンストラクタが origin を受け付け、origin 付きリクエスト
    で接続が確立できること」のみ。Origin ヘッダーが実際に送信されることの
    検証は、サーバー側 Origin 検証の e2e テスト (403 の観測) で行い、
    本テストはその実装までのスモークテストである。
    """
    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    server.on_session_ready(on_session_ready)
    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
        origin="https://example.com",
    )

    connected = await client.connect()
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_client_communication(test_certificates):
    """Server と Client 間で WebTransport 通信ができることを確認"""
    from webtransport.h3 import Client, Server

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

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_stream_data(session_id, stream_id, data, addr):
        server_received_data.append(data)
        server_data_received.set()
        await server.send_stream_data(addr, stream_id, b"pong", fin=True)

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

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

    stream_id = await client.open_stream()
    assert stream_id >= 0

    await client.send_stream_data(stream_id, b"ping", fin=True)

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
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_client_datagram_communication(test_certificates):
    """Server と Client 間で WebTransport データグラムが送受信できることを確認

    Quarter Stream ID のエンコード / デコード込みで、ペイロードだけが
    コールバックに届くことを検証する。
    """
    from webtransport.h3 import Client, Server

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

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_datagram(session_id, data, addr):
        server_received_data.append(data)
        server_data_received.set()
        # エコー返信
        await server.send_datagram(addr, session_id, b"pong-dg")

    server.on_session_ready(on_session_ready)
    server.on_datagram(on_datagram)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_client_datagram(data):
        client_received_data.append(data)
        client_data_received.set()

    client.on_datagram(on_client_datagram)

    connected = await client.connect()
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
    await client.send_datagram(b"ping-dg")

    await asyncio.wait_for(server_data_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)

    assert server_received_data == [b"ping-dg"]
    assert client_received_data == [b"pong-dg"]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_multiple_streams_communication(test_certificates):
    """同一セッションで複数 bidi ストリームが独立して送受信できることを確認"""
    from webtransport.h3 import Client, Server

    # stream_id -> 受信データ
    server_received = {}
    client_received = {}
    expected_streams = 3
    session_ready_event = asyncio.Event()
    server_all_received = asyncio.Event()
    client_all_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_stream_data(session_id, stream_id, data, addr):
        server_received[stream_id] = data
        # ストリームごとに応答を返す
        await server.send_stream_data(addr, stream_id, b"reply-" + data, fin=True)
        if len(server_received) >= expected_streams:
            server_all_received.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_client_stream_data(stream_id, data):
        client_received[stream_id] = data
        if len(client_received) >= expected_streams:
            client_all_received.set()

    client.on_stream_data(on_client_stream_data)

    connected = await client.connect()
    assert connected is True

    stream_ids = []
    for index in range(expected_streams):
        stream_id = await client.open_stream()
        assert stream_id >= 0
        stream_ids.append(stream_id)
        await client.send_stream_data(stream_id, f"msg-{index}".encode(), fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
    await asyncio.wait_for(server_all_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_all_received.wait(), timeout=5.0)

    assert len(server_received) == expected_streams
    assert len(client_received) == expected_streams
    for index, stream_id in enumerate(stream_ids):
        assert server_received[stream_id] == f"msg-{index}".encode()
        assert client_received[stream_id] == f"reply-msg-{index}".encode()

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_session_close_notifies_server(test_certificates):
    """Client の close で Server 側に SESSION_CLOSED が届くことを確認"""
    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()
    session_closed_event = asyncio.Event()
    closed_session_ids = []

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_session_closed(session_id, addr):
        closed_session_ids.append(session_id)
        session_closed_event.set()

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    connected = await client.connect()
    assert connected is True
    client_session_id = client.session_id

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)

    # クライアントがセッションを閉じる
    await client.close()
    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)

    await asyncio.wait_for(session_closed_event.wait(), timeout=5.0)

    assert closed_session_ids == [client_session_id]

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await server.stop()


@pytest.mark.asyncio
async def test_server_resets_client_stream(test_certificates):
    """Server が reset_stream すると Client に STREAM_RESET が届くことを確認"""
    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()
    server_data_received = asyncio.Event()
    client_reset_received = asyncio.Event()
    reset_info = {}

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_stream_data(session_id, stream_id, data, addr):
        server_data_received.set()
        # 受信したストリームをアプリケーションエラーでリセットする
        await server.reset_stream(addr, stream_id, error_code=0x01)

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_client_stream_reset(stream_id, error_code):
        reset_info["stream_id"] = stream_id
        reset_info["error_code"] = error_code
        client_reset_received.set()

    client.on_stream_reset(on_client_stream_reset)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.open_stream()
    assert stream_id >= 0
    await client.send_stream_data(stream_id, b"to-be-reset", fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
    await asyncio.wait_for(server_data_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_reset_received.wait(), timeout=5.0)

    assert reset_info["stream_id"] == stream_id
    assert reset_info["error_code"] == 0x01

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_client_resets_server_stream(test_certificates):
    """Client が reset_stream すると Server に STREAM_RESET が届くことを確認"""
    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()
    server_reset_received = asyncio.Event()
    reset_info = {}

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_stream_reset(session_id, stream_id, error_code, addr):
        reset_info["stream_id"] = stream_id
        reset_info["error_code"] = error_code
        server_reset_received.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_reset(on_stream_reset)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    connected = await client.connect()
    assert connected is True

    stream_id = await client.open_stream()
    assert stream_id >= 0
    await client.send_stream_data(stream_id, b"opening", fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)

    # クライアント側からストリームをリセットする
    await client.reset_stream(stream_id, error_code=0x02)

    await asyncio.wait_for(server_reset_received.wait(), timeout=5.0)

    assert reset_info["stream_id"] == stream_id
    assert reset_info["error_code"] == 0x02

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_chunked_stream_data(test_certificates):
    """同一ストリームへ複数回送信したデータが結合されて届くことを確認

    高レベル API の STREAM_DATA コールバックは fin を渡さないため、
    固定長プロトコルで完了を判定する。
    """
    from webtransport.h3 import Client, Server

    expected_payload = b"AAAA" + b"BBBB" + b"CCCC"
    server_buffer = bytearray()
    client_received = []
    session_ready_event = asyncio.Event()
    server_complete = asyncio.Event()
    client_data_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_stream_data(session_id, stream_id, data, addr):
        server_buffer.extend(data)
        if len(server_buffer) >= len(expected_payload):
            server_complete.set()
            await server.send_stream_data(
                addr,
                stream_id,
                b"echo:" + bytes(server_buffer),
                fin=True,
            )

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_client_stream_data(stream_id, data):
        client_received.append(data)
        client_data_received.set()

    client.on_stream_data(on_client_stream_data)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.open_stream()
    assert stream_id >= 0
    # fin=False で分割送信し、最後に fin=True で閉じる
    await client.send_stream_data(stream_id, b"AAAA", fin=False)
    await client.send_stream_data(stream_id, b"BBBB", fin=False)
    await client.send_stream_data(stream_id, b"CCCC", fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
    await asyncio.wait_for(server_complete.wait(), timeout=5.0)
    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)

    assert bytes(server_buffer) == expected_payload
    assert client_received == [b"echo:" + expected_payload]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_multiple_datagrams(test_certificates):
    """同一セッションで複数データグラムが独立して送受信できることを確認"""
    from webtransport.h3 import Client, Server

    expected_count = 5
    server_received = []
    client_received = []
    session_ready_event = asyncio.Event()
    server_all_received = asyncio.Event()
    client_all_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_datagram(session_id, data, addr):
        server_received.append(data)
        await server.send_datagram(addr, session_id, b"ack-" + data)
        if len(server_received) >= expected_count:
            server_all_received.set()

    server.on_session_ready(on_session_ready)
    server.on_datagram(on_datagram)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_client_datagram(data):
        client_received.append(data)
        if len(client_received) >= expected_count:
            client_all_received.set()

    client.on_datagram(on_client_datagram)

    connected = await client.connect()
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)

    for index in range(expected_count):
        await client.send_datagram(f"dg-{index}".encode())

    await asyncio.wait_for(server_all_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_all_received.wait(), timeout=5.0)

    assert server_received == [f"dg-{index}".encode() for index in range(expected_count)]
    assert client_received == [f"ack-dg-{index}".encode() for index in range(expected_count)]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_stream_and_datagram_combined(test_certificates):
    """同一セッションでストリームとデータグラムを同時に送れることを確認"""
    from webtransport.h3 import Client, Server

    server_stream_data = []
    server_datagrams = []
    client_stream_data = []
    client_datagrams = []
    session_ready_event = asyncio.Event()
    server_stream_received = asyncio.Event()
    server_datagram_received = asyncio.Event()
    client_stream_received = asyncio.Event()
    client_datagram_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_stream_data(session_id, stream_id, data, addr):
        server_stream_data.append(data)
        server_stream_received.set()
        await server.send_stream_data(addr, stream_id, b"stream-pong", fin=True)

    async def on_datagram(session_id, data, addr):
        server_datagrams.append(data)
        server_datagram_received.set()
        await server.send_datagram(addr, session_id, b"dg-pong")

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
    server.on_datagram(on_datagram)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_client_stream_data(stream_id, data):
        client_stream_data.append(data)
        client_stream_received.set()

    async def on_client_datagram(data):
        client_datagrams.append(data)
        client_datagram_received.set()

    client.on_stream_data(on_client_stream_data)
    client.on_datagram(on_client_datagram)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.open_stream()
    assert stream_id >= 0
    await client.send_stream_data(stream_id, b"stream-ping", fin=True)
    await client.send_datagram(b"dg-ping")

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
    await asyncio.wait_for(server_stream_received.wait(), timeout=5.0)
    await asyncio.wait_for(server_datagram_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_stream_received.wait(), timeout=5.0)
    await asyncio.wait_for(client_datagram_received.wait(), timeout=5.0)

    assert server_stream_data == [b"stream-ping"]
    assert server_datagrams == [b"dg-ping"]
    assert client_stream_data == [b"stream-pong"]
    assert client_datagrams == [b"dg-pong"]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_unidirectional_stream(test_certificates):
    """クライアント起点の単方向ストリームがサーバーに届くことを確認"""
    from webtransport.h3 import Client, Server

    server_received = []
    session_ready_event = asyncio.Event()
    server_data_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_stream_data(session_id, stream_id, data, addr):
        server_received.append((stream_id, data))
        server_data_received.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    connected = await client.connect()
    assert connected is True

    stream_id = await client.open_stream(unidirectional=True)
    assert stream_id >= 0
    await client.send_stream_data(stream_id, b"uni-payload", fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
    await asyncio.wait_for(server_data_received.wait(), timeout=5.0)

    assert server_received == [(stream_id, b"uni-payload")]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_large_stream_payload(test_certificates):
    """比較的大きなストリームペイロードが往復することを確認"""
    from webtransport.h3 import Client, Server

    # 32 KiB。QUIC パケット境界をまたぐサイズを選ぶ
    payload = bytes((index % 256) for index in range(32 * 1024))
    server_buffer = bytearray()
    client_buffer = bytearray()
    session_ready_event = asyncio.Event()
    server_complete = asyncio.Event()
    client_complete = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_stream_data(session_id, stream_id, data, addr):
        server_buffer.extend(data)
        if len(server_buffer) >= len(payload):
            server_complete.set()
            await server.send_stream_data(addr, stream_id, bytes(server_buffer), fin=True)

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_client_stream_data(stream_id, data):
        client_buffer.extend(data)
        if len(client_buffer) >= len(payload):
            client_complete.set()

    client.on_stream_data(on_client_stream_data)

    connected = await client.connect()
    assert connected is True

    stream_id = await client.open_stream()
    assert stream_id >= 0
    await client.send_stream_data(stream_id, payload, fin=True)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
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
async def test_client_session_ready_callback(test_certificates):
    """Client 側の on_session_ready が正しい session_id で呼ばれることを確認"""
    from webtransport.h3 import Client, Server

    server_session_ids = []
    client_session_ids = []
    server_ready = asyncio.Event()
    client_ready = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_session_ready(session_id, addr):
        server_session_ids.append(session_id)
        server_ready.set()

    server.on_session_ready(on_server_session_ready)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_client_session_ready(session_id):
        client_session_ids.append(session_id)
        client_ready.set()

    client.on_session_ready(on_client_session_ready)

    connected = await client.connect()
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(server_ready.wait(), timeout=5.0)
    await asyncio.wait_for(client_ready.wait(), timeout=5.0)

    assert len(server_session_ids) == 1
    assert len(client_session_ids) == 1
    assert client_session_ids[0] == client.session_id
    assert client_session_ids[0] == server_session_ids[0]
    assert client.session_id >= 0

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()
