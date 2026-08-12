"""webtransport.h3 (WebTransport over HTTP/3) テスト

高レベル API (Client / Server) のテストに加え、同一 QUIC 接続上に
複数セッションを確立する検証では低レベル API (quic.Connection +
h3.Session) を使う。
"""

import asyncio
import socket
from dataclasses import dataclass, field

import pytest
from conftest import _encode_wt_datagram

from webtransport import h3 as h3_low
from webtransport import quic
from webtransport.h3 import Server


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
async def test_origin_verification_accepts_allowed_origin(test_certificates):
    """許可されたオリジンからの接続が 2xx で受理されることを確認する

    allowed_origins に含まれる Origin ヘッダーを送るクライアントの接続は
    受理され、クライアント側の SESSION_READY (2xx 応答の受信) とサーバー
    側のセッション確立の両方が発生する。
    """
    from webtransport.h3 import Client, Server

    server_session_ready = asyncio.Event()
    client_session_ready = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        allowed_origins=["https://allowed.example.com"],
    )

    async def on_server_session_ready(session_id, addr):
        server_session_ready.set()

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
        origin="https://allowed.example.com",
    )

    async def on_client_session_ready(session_id):
        client_session_ready.set()

    client.on_session_ready(on_client_session_ready)

    connected = await client.connect()
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(server_session_ready.wait(), timeout=5.0)
    await asyncio.wait_for(client_session_ready.wait(), timeout=5.0)

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_origin_verification_rejects_disallowed_origin(test_certificates):
    """許可されていないオリジンからの接続が拒否されることを確認する

    allowed_origins に含まれない Origin ヘッダーを送るクライアントの接続は
    拒否され、サーバー側でセッションが確立されない (on_session_ready が
    発火しない)。このテストが観測できるのはセッション不確立のみであり、
    403 応答の受信はクライアントが非 200 をイベント化しないため観測
    対象外である。
    """
    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        allowed_origins=["https://allowed.example.com"],
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
        origin="https://disallowed.example.com",
    )

    connected = await client.connect()
    # QUIC トランスポートの接続は成功するが、CONNECT リクエスト自体は拒否される
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    # セッションが確立されないことを確認する
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_origin_verification_accepts_without_origin(test_certificates):
    """allowed_origins 設定時でも Origin ヘッダー無しの接続は受理されることを確認する

    仕様上 Origin ヘッダーは非ブラウザクライアントでは OPTIONAL であり、
    Origin ヘッダーが無いリクエストは従来どおり受理する。
    """
    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        allowed_origins=["https://allowed.example.com"],
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
async def test_origin_verification_accepts_without_allowed_origins(test_certificates):
    """allowed_origins 未設定時は origin 付きの接続も受理されることを確認する

    許可リストが未設定 (空) の場合は従来どおり全オリジンを受理する。
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
async def test_large_echo_over_initial_recv_window(test_certificates):
    """初期受信ウィンドウを超える大容量 echo 転送が完了することを確認

    受信フロー制御の再開放が無ければ、サーバーの初期受信ウィンドウ
    (ストリーム 256 KiB) でクライアントの送信がブロックされ、512 KiB の
    echo 転送は完了しない。再開放により MAX_STREAM_DATA が送出され、
    クライアントの送信が止まらずに全量が往復することを確認する。
    コネクションレベルの再開放 (MAX_DATA) は 512 KiB がコネクションの
    初期ウィンドウ (1 MiB) に収まるため、本テストでは検証しない
    (test_quic_recv_flow_control.py で検証する)
    """
    from webtransport.h3 import Client, Server

    payload = b"x" * (512 * 1024)
    server_received = 0
    client_received = 0
    session_ready_event = asyncio.Event()
    echo_completed = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    async def on_stream_data(session_id, stream_id, data, addr):
        nonlocal server_received
        server_received += len(data)
        # 受信した断片をそのままエコーバックする (FIN は付けない)
        await server.send_stream_data(addr, stream_id, data)

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
        nonlocal client_received
        client_received += len(data)
        if client_received == len(payload):
            echo_completed.set()

    client.on_stream_data(on_client_stream_data)

    connected = await client.connect()
    assert connected is True

    # クライアントの受信ループを起動してからセッション確立を待つ。
    # run() を先に回さないと、クライアントはサーバーの応答 (200 OK や
    # MAX_STREAM_DATA / MAX_DATA) を受信・処理できず、送信が進まない
    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    try:
        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)

        stream_id = await client.open_stream()
        assert stream_id >= 0

        # 512 KiB を 1 回の呼び出しで送信する
        # (送信と受信は client.run() のループが処理する)
        await client.send_stream_data(stream_id, payload)

        # 再開放が機能しないと echo が完了せずタイムアウトする
        await asyncio.wait_for(echo_completed.wait(), timeout=60.0)

        assert server_received == len(payload)
        assert client_received == len(payload)
    finally:
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
    server_data_received = asyncio.Event()
    server_reset_received = asyncio.Event()
    reset_info = {}
    expected_session_id = {}

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        expected_session_id["session_id"] = session_id
        session_ready_event.set()

    async def on_stream_data(
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        server_data_received.set()

    async def on_stream_reset(
        session_id: int,
        stream_id: int,
        error_code: int,
        addr: tuple[str, int],
    ) -> None:
        reset_info["session_id"] = session_id
        reset_info["stream_id"] = stream_id
        reset_info["error_code"] = error_code
        server_reset_received.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
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
    # サーバー側でデータ受信 (stream_info_ への登録) を確認してからリセットする
    await asyncio.wait_for(server_data_received.wait(), timeout=5.0)

    # クライアント側からストリームをリセットする
    await client.reset_stream(stream_id, error_code=0x02)

    await asyncio.wait_for(server_reset_received.wait(), timeout=5.0)

    assert reset_info["stream_id"] == stream_id
    assert reset_info["error_code"] == 0x02
    # リセットされたストリームの属するセッション ID が渡される
    assert reset_info["session_id"] == expected_session_id["session_id"]

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
async def test_server_unidirectional_stream(test_certificates):
    """サーバー起点の単方向ストリームがクライアントに届くことを確認

    test_unidirectional_stream の逆方向。クライアント側の変更は伴わない。
    """
    from webtransport.h3 import Client, Server

    client_received = []
    opened_stream_id = None
    opened_event = asyncio.Event()
    client_data_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        nonlocal opened_stream_id
        opened_stream_id = await server.open_stream(addr, session_id)
        opened_event.set()
        if opened_stream_id >= 0:
            await server.send_stream_data(addr, opened_stream_id, b"server-uni-payload", fin=True)

    server.on_session_ready(on_session_ready)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    async def on_stream_data(stream_id, data):
        client_received.append((stream_id, data))
        client_data_received.set()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    client.on_stream_data(on_stream_data)

    connected = await client.connect()
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(opened_event.wait(), timeout=5.0)
    assert opened_stream_id is not None
    assert opened_stream_id >= 0
    # RFC 9000 Section 2.1 Table 1 によりサーバー起点の単方向ストリームは % 4 == 3
    assert opened_stream_id % 4 == 3

    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)
    assert client_received == [(opened_stream_id, b"server-uni-payload")]

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_open_stream_errors():
    """Server.open_stream のエラーパスを確認

    クライアント接続が無いアドレスへの呼び出しは -1、双方向ストリームの
    指定は NotImplementedError を上げる。
    """
    from webtransport.h3 import Server

    server = Server(host="127.0.0.1", port=0)
    await server.start()

    # 接続が無いクライアントアドレスには -1 を返す
    stream_id = await server.open_stream(("127.0.0.1", 9999), 0)
    assert stream_id == -1

    # 双方向ストリームは対象外のため NotImplementedError を上げる
    with pytest.raises(NotImplementedError):
        await server.open_stream(("127.0.0.1", 9999), 0, unidirectional=False)

    await server.stop()


@pytest.mark.asyncio
async def test_server_open_stream_invalid_session_id(test_certificates):
    """存在しないセッション ID で open_stream を呼ぶと -1 を返す

    h3 側の登録失敗時は開いた QUIC ストリームを閉じるため、クライアントは
    RESET_STREAM を受けて接続を維持でき、後続のストリーム送信も機能する。
    """
    from webtransport.h3 import Client, Server

    client_received = []
    client_resets = []
    client_addr = None
    client_session_id = None
    session_ready_event = asyncio.Event()
    client_data_received = asyncio.Event()
    client_reset_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        nonlocal client_addr, client_session_id
        client_addr = addr
        client_session_id = session_id
        session_ready_event.set()

    server.on_session_ready(on_session_ready)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    async def on_stream_data(stream_id, data):
        client_received.append((stream_id, data))
        client_data_received.set()

    async def on_stream_reset(stream_id, error_code):
        client_resets.append((stream_id, error_code))
        client_reset_received.set()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    client.on_stream_data(on_stream_data)
    client.on_stream_reset(on_stream_reset)

    connected = await client.connect()
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
    assert client_addr is not None
    assert client_session_id is not None

    # 存在しないセッション ID には -1 を返す
    invalid_stream_id = await server.open_stream(client_addr, 9999)
    assert invalid_stream_id == -1

    # 開いた QUIC ストリームの RESET_STREAM がクライアントに届き、
    # クライアントは接続を維持する
    await asyncio.wait_for(client_reset_received.wait(), timeout=5.0)
    assert len(client_resets) == 1
    assert client_resets[0][1] == 0

    # 正しいセッション ID では引き続きストリームを開いて送信できる
    stream_id = await server.open_stream(client_addr, client_session_id)
    assert stream_id >= 0
    await server.send_stream_data(client_addr, stream_id, b"after-invalid", fin=True)

    await asyncio.wait_for(client_data_received.wait(), timeout=5.0)
    assert client_received == [(stream_id, b"after-invalid")]

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


class _LowLevelClient:
    """低レベル API (quic.Connection + h3.Session) で構築するクライアント

    高レベル Client は 1 接続 1 セッションのため、同一 QUIC 接続上に
    複数の WebTransport セッションを確立する検証には低レベル API を使う。
    接続手順は高レベル Client の connect (src/webtransport/h3/client.py) を
    参考にしている
    """

    def __init__(self, server_port: int) -> None:
        self._server_addr: tuple[str, int] = ("127.0.0.1", server_port)
        self._socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind(("127.0.0.1", 0))
        self._local_addr: tuple[str, int] = (
            "127.0.0.1",
            self._socket.getsockname()[1],
        )

        quic_config = quic.Config()
        quic_config.alpn_protocols = ["h3"]
        quic_config.verify_peer = False
        quic_config.server_name = "127.0.0.1"
        self._quic_connection: quic.Connection = quic.Connection.create_client(
            quic_config,
            self._local_addr,
            self._server_addr,
        )
        h3_config = h3_low.Config()
        h3_config.is_server = False
        self._h3_session: h3_low.Session = h3_low.Session.create_client(h3_config)

        # QUIC 層で生成済みだがワイヤに送出していないパケット
        # (RESET_STREAM_AT の検証で、データがリセットより先に届かない
        # 順序を作るために使う)
        self._withheld_packets: list[quic.Packet] = []

    def close(self) -> None:
        """QUIC 接続とソケットを閉じる

        同期メソッドのため CONNECTION_CLOSE パケットは送出しない
        (サーバー側の終了検知はテストの後片付けが server.stop() で
        行うため、このクラスでは不要)
        """
        self._quic_connection.close()
        self._socket.close()

    async def _send_packet(self) -> None:
        """QUIC 層のパケットを送信する"""
        loop = asyncio.get_running_loop()
        packet = self._quic_connection.send()
        if packet is None:
            return
        await loop.sock_sendto(self._socket, packet.data, self._server_addr)

    async def _pump(self) -> None:
        """h3 層の送信データを QUIC に渡して送信する"""
        for stream_id, stream_data, fin in self._h3_session.get_streams_to_send():
            self._quic_connection.send_stream_data(stream_id, stream_data, fin)
        await self._send_packet()

    async def _send_quic_only(self) -> None:
        """QUIC 層の送信だけを実行する

        h3 層の get_streams_to_send を呼ばないため、h3 層に積まれた
        WT ヘッダーはワイヤに出ない。データ未受信のままリセットする
        検証で使う
        """
        await self._send_packet()

    async def _receive(self) -> None:
        """QUIC パケットを 1 件受信して処理する (タイムアウト時は何もしない)"""
        loop = asyncio.get_running_loop()
        try:
            data, raw_remote = await asyncio.wait_for(
                loop.sock_recvfrom(self._socket, 65535),
                timeout=0.1,
            )
        except TimeoutError:
            return
        remote = (str(raw_remote[0]), raw_remote[1])
        self._quic_connection.receive(data, self._local_addr, remote)

    def _process_quic_events(self) -> bool:
        """QUIC イベントを処理して h3 層に流す

        Returns:
            接続が継続する場合は True
        """
        while True:
            quic_event = self._quic_connection.next_event()
            if quic_event is None:
                break
            if quic_event.type == quic.EventType.STREAM_DATA:
                self._h3_session.receive_stream_data(
                    quic_event.stream_id,
                    quic_event.data,
                    quic_event.fin,
                )
            elif quic_event.type == quic.EventType.DATAGRAM:
                self._h3_session.receive_datagram(quic_event.data)
            elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                return False
        return True

    async def connect(self) -> bool:
        """QUIC ハンドシェイクと制御ストリームのバインドを行う

        Returns:
            接続に成功した場合は True
        """
        await self._pump()
        handshake_done = False
        while not handshake_done:
            await self._receive()
            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break
                if quic_event.type == quic.EventType.HANDSHAKE_COMPLETED:
                    handshake_done = True
                    # 以降のイベント (サーバーの SETTINGS 等) は
                    # 次の SETTINGS 待ちループで処理する
                    break
                elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                    return False
            await self._pump()

        control_stream_id = self._quic_connection.open_stream(False)
        self._h3_session.bind_control_stream(control_stream_id)
        encoder_stream_id = self._quic_connection.open_stream(False)
        self._h3_session.bind_qpack_encoder_stream(encoder_stream_id)
        decoder_stream_id = self._quic_connection.open_stream(False)
        self._h3_session.bind_qpack_decoder_stream(decoder_stream_id)
        await self._pump()

        # サーバーの SETTINGS を受信するまで待機
        # サーバーの制御ストリームは server.py の _setup_streams が
        # 最初に開く単方向ストリーム (stream_id=3) のため、その受信を
        # SETTINGS 受信の完了とみなす (高レベル Client の connect と同じ)
        settings_received = False
        max_attempts = 100
        attempt = 0
        while not settings_received and attempt < max_attempts:
            await self._receive()
            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break
                if quic_event.type == quic.EventType.STREAM_DATA:
                    self._h3_session.receive_stream_data(
                        quic_event.stream_id,
                        quic_event.data,
                        quic_event.fin,
                    )
                    if quic_event.stream_id == 3:
                        settings_received = True
                elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                    return False
            await self._pump()
            attempt += 1
        return settings_received

    async def establish_session(self) -> int:
        """WebTransport セッションを確立してセッション ID を返す

        Returns:
            セッション ID。接続が閉じた場合は -1
        """
        request_stream_id = self._quic_connection.open_stream(True)
        assert (
            self._h3_session.connect(
                request_stream_id,
                f"https://127.0.0.1:{self._server_addr[1]}/webtransport",
            )
            is True
        )
        await self._pump()

        while True:
            await self._receive()
            if not self._process_quic_events():
                return -1
            while True:
                h3_event = self._h3_session.next_event()
                if h3_event is None:
                    break
                if h3_event.type == h3_low.EventType.SESSION_READY:
                    return h3_event.session_id
            await self._pump()

    async def establish_two_sessions(self) -> tuple[int, int]:
        """同一 QUIC 接続上に 2 セッションを確立する

        Returns:
            (1 つ目のセッション ID, 2 つ目のセッション ID)
        """
        first_session_id = await asyncio.wait_for(self.establish_session(), timeout=5.0)
        second_session_id = await asyncio.wait_for(self.establish_session(), timeout=5.0)
        assert first_session_id >= 0
        assert second_session_id >= 0
        assert first_session_id != second_session_id
        return first_session_id, second_session_id

    async def open_stream(self, session_id: int) -> int:
        """セッションに双方向データストリームを開く

        WT ヘッダーは h3 層のキューに積まれるだけで、この時点では送信しない。
        送信は send_stream_data / reset_stream の QUIC 側送出に依存する。
        -1 検証テストの決定的性はこの「送信しない」前提に依存している
        (送信するとサーバー側の stream_info_ に登録され、セッション ID が
        復元可能になる)

        Returns:
            ストリーム ID
        """
        stream_id = self._quic_connection.open_stream(True)
        assert self._h3_session.open_stream(session_id, stream_id, False) is True
        return stream_id

    async def send_stream_data(self, stream_id: int, data: bytes) -> None:
        """ストリームにデータを送信する"""
        self._h3_session.send_stream_data(stream_id, data)
        await self._pump()

    async def send_stream_data_withheld(self, stream_id: int, data: bytes) -> None:
        """ストリームにデータを送信するが、生成したパケットは送出せず保持する

        QUIC 層 (ngtcp2) にデータを書き込み済み (tx offset が前進) にする一方、
        ワイヤには出さない。データがリセットより先に届かない順序を作る
        RESET_STREAM_AT の検証で使う。1 回の send() は 1 パケットしか返さない
        ため、データは 1 パケットに収まるサイズを渡すこと。この検証の決定的性
        は、データストリームより小さい ID のストリーム (CONNECT リクエスト /
        制御ストリーム) に残留データがないことにも依存する (send() は
        stream_buffers_ をストリーム ID 昇順で処理するため、残留があると
        データストリームのパケットが生成されず、データが stream_buffers_ に
        残ったままリセットで破棄される)
        """
        self._h3_session.send_stream_data(stream_id, data)
        for stream_id_to_send, stream_data, fin in self._h3_session.get_streams_to_send():
            self._quic_connection.send_stream_data(stream_id_to_send, stream_data, fin)
        packet = self._quic_connection.send()
        # パケットが生成されない場合 (cwnd 枯渇等) は、データが stream_buffers_
        # に残ったままリセットで破棄され、失敗モードが不明瞭になるため
        # ここで明示的に失敗させる
        assert packet is not None
        self._withheld_packets.append(packet)

    async def send_withheld_packets(self) -> None:
        """保留していたパケットを送信する"""
        loop = asyncio.get_running_loop()
        for packet in self._withheld_packets:
            await loop.sock_sendto(self._socket, packet.data, self._server_addr)
        self._withheld_packets.clear()

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセットする

        QUIC と h3 層の両方にリセットを通知し、QUIC 層の送信のみを
        実行する。h3 層の get_streams_to_send を呼ぶと積まれた WT
        ヘッダーが送信されてしまうため、WT ヘッダー未受信のまま
        リセットする検証が決定的でなくなる
        """
        self._quic_connection.reset_stream(stream_id, error_code)
        self._h3_session.reset_stream(stream_id, error_code)
        await self._send_quic_only()

    async def _receive_datagram(self) -> h3_low.Event | None:
        """データグラムを受信して Datagram イベントを返す

        最大 5 秒待ち、受信できなかった場合は None を返す。Datagram より
        先に積まれた h3 イベント (セッション終了通知等) は消費して捨てる
        """
        deadline = asyncio.get_running_loop().time() + 5.0
        while True:
            await self._receive()
            if not self._process_quic_events():
                return None
            while True:
                event = self._h3_session.next_event()
                if event is None:
                    break
                if event.type == h3_low.EventType.DATAGRAM:
                    return event
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.01)


@dataclass
class _ResetTestServerInfo:
    """STREAM_RESET 検証用サーバーの観測結果"""

    session_ids: list[int] = field(default_factory=list)
    sessions_ready: asyncio.Event = field(default_factory=asyncio.Event)
    data_session_id: int | None = None
    data_received: asyncio.Event = field(default_factory=asyncio.Event)
    reset_session_id: int | None = None
    reset_stream_id: int | None = None
    reset_received: asyncio.Event = field(default_factory=asyncio.Event)


async def _start_reset_test_server(
    test_certificates,
    expected_sessions: int,
) -> tuple[Server, asyncio.Task, _ResetTestServerInfo]:
    """STREAM_RESET 検証用の高レベル Server を起動する

    Args:
        test_certificates: テスト用証明書フィクスチャ
        expected_sessions: セッション確立待ちの数

    Returns:
        (server, server_task, info) のタプル
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    info = _ResetTestServerInfo()

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        info.session_ids.append(session_id)
        if len(info.session_ids) == expected_sessions:
            info.sessions_ready.set()

    async def on_stream_data(
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        info.data_session_id = session_id
        info.data_received.set()

    async def on_stream_reset(
        session_id: int,
        stream_id: int,
        error_code: int,
        addr: tuple[str, int],
    ) -> None:
        info.reset_session_id = session_id
        info.reset_stream_id = stream_id
        info.reset_received.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
    server.on_stream_reset(on_stream_reset)

    # サーバーの起動を完了させてからタスクを作成する
    # (run() は未開始状態だと RuntimeError を上げるため)
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    return server, server_task, info


async def _cleanup_reset_test_server(
    server: Server,
    server_task: asyncio.Task,
    client: _LowLevelClient,
) -> None:
    """_LowLevelClient を使う e2e テストの後片付けを行う

    サーバータスクが例外終了していた場合は、テスト本体の失敗を
    覆い隠さないよう元の例外を raise する
    """
    if server_task.done():
        exception = server_task.exception()
        if exception is not None:
            raise exception
    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await server.stop()
    client.close()


@dataclass
class _SessionClosedServerInfo:
    """CONNECT ストリームのクローズ (リセット / FIN) によるセッション終了検知の観測結果"""

    session_ids: list[int] = field(default_factory=list)
    sessions_ready: asyncio.Event = field(default_factory=asyncio.Event)
    closed_session_ids: list[int] = field(default_factory=list)
    session_closed: asyncio.Event = field(default_factory=asyncio.Event)
    data_session_id: int | None = None
    data_received: asyncio.Event = field(default_factory=asyncio.Event)


async def _start_session_closed_server(
    test_certificates,
    expected_sessions: int,
) -> tuple[Server, asyncio.Task, _SessionClosedServerInfo]:
    """セッション終了検知検証用の高レベル Server を起動する

    on_session_ready / on_session_closed / on_stream_data を観測用のリストと
    イベントに記録する。sessions_ready は expected_sessions 件目の
    セッション確立で発火する。

    Returns:
        (server, server_task, info) のタプル
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    info = _SessionClosedServerInfo()

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        info.session_ids.append(session_id)
        if len(info.session_ids) == expected_sessions:
            info.sessions_ready.set()

    async def on_session_closed(session_id: int, addr: tuple[str, int]) -> None:
        info.closed_session_ids.append(session_id)
        info.session_closed.set()

    async def on_stream_data(
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        info.data_session_id = session_id
        info.data_received.set()

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    return server, server_task, info


@pytest.mark.asyncio
async def test_stream_reset_second_session_id(test_certificates):
    """複数セッション確立時にリセットしたストリームのセッション ID が渡ることを確認

    同一 QUIC 接続上に 2 セッションを確立し、2 つ目のセッションでクライアントが
    開いたデータストリームを、サーバー側の on_stream_data で受信を確認してから
    リセットすると、2 つ目のセッション ID が on_stream_reset に渡る
    (旧実装ではセッション ID 集合の先頭要素が渡っていた)
    """
    server, server_task, info = await _start_reset_test_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

        # 同一 QUIC 接続上に 2 セッションを確立する
        first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)
        assert info.session_ids == [first_session_id, second_session_id]

        # 2 つ目のセッションでデータストリームを開いて送信する
        stream_id = await client.open_stream(second_session_id)
        await client.send_stream_data(stream_id, b"payload")

        # サーバー側の受信を確認してからリセットする
        await asyncio.wait_for(info.data_received.wait(), timeout=5.0)
        assert info.data_session_id == second_session_id

        await client.reset_stream(stream_id)

        # リセットされたストリームの属するセッション ID が渡る
        await asyncio.wait_for(info.reset_received.wait(), timeout=5.0)
        assert info.reset_stream_id == stream_id
        assert info.reset_session_id == second_session_id
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_stream_reset_at_recovers_session_id(test_certificates):
    """書き込み済みデータのあるストリームのリセットでセッション ID が復元される

    データパケットを保留してリセット送出パケットを先に届ける構成で、
    RESET_STREAM_AT (draft-ietf-webtrans-http3-16 Section 4.4 の MUST) により
    セッション ID が復元されることを確認する。RESET_STREAM_AT の Reliable Size
    は書き込み済みオフセット全体に設定されるため、ピアはデータ到着まで
    リセットを確定しない (draft-ietf-quic-reliable-stream-reset-09 Section 5.3
    の Size Known → Data Recvd 遷移)。後から届いた WT ヘッダーでストリームが
    セッションに関連付けられてからリセットが確定し、on_stream_reset に正しい
    セッション ID が渡る (データ未送信のままリセットした場合は -1 になる。
    test_stream_reset_before_data_received_minus_one 参照)
    """
    server, server_task, info = await _start_reset_test_server(
        test_certificates, expected_sessions=1
    )

    client = _LowLevelClient(server.actual_port)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

        session_id = await client.establish_session()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)

        # データストリームを開いてデータを送信するが、パケットは保留する
        # (QUIC 層に書き込み済みの状態を作りつつ、データがリセットより先に
        # 届かない順序にする)
        stream_id = await client.open_stream(session_id)
        await client.send_stream_data_withheld(stream_id, b"payload")

        # リセット送出パケットを先に送信する (RESET_STREAM_AT)
        await client.reset_stream(stream_id)

        # 保留していたデータパケットを送信する。ngtcp2 の writev_stream は
        # アプリのデータ (vec) を直接パケットに書く設計のため、リセット送出
        # パケットに未 ACK データは同梱されない。データはこの保留パケット
        # 経由で配信され、RESET_STREAM_AT の Reliable Size によりピアは
        # データ到着までリセットを確定しない
        await client.send_withheld_packets()

        # 後から届いた WT ヘッダーでセッション ID が復元される
        await asyncio.wait_for(info.reset_received.wait(), timeout=5.0)
        assert info.reset_stream_id == stream_id
        assert info.reset_session_id == session_id
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_stream_reset_before_data_received_minus_one(test_certificates):
    """WT ヘッダー未受信のままリセットされたストリームには -1 が渡ることを確認

    open_stream と reset_stream の間に送信処理を挟まない (WT ヘッダーが先に
    届くと stream_info_ に登録され、-1 が決定的にならない)。セッションとの
    関連付けはストリーム先頭のヘッダー経由のみであり (draft-ietf-webtrans-http3-16
    Section 4.4)、データ未書き込みのリセットは従来どおり RESET_STREAM が送出
    される (Reliable Size 0 の RESET_STREAM_AT は RESET_STREAM と等価。
    draft-ietf-quic-reliable-stream-reset-09 Section 5)。ヘッダー未受信のまま
    リセットされたストリームは復元できない。旧実装では無関係なセッション ID が
    渡っていたケース
    """
    server, server_task, info = await _start_reset_test_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

        _first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)

        # データストリームを開くが、データは送信しない
        stream_id = await client.open_stream(second_session_id)

        # 送信処理を挟まずにリセットする
        await client.reset_stream(stream_id)

        # セッション ID を復元できないため -1 が渡る
        await asyncio.wait_for(info.reset_received.wait(), timeout=5.0)
        assert info.reset_stream_id == stream_id
        assert info.reset_session_id == -1
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_stream_reset_connect_stream_session_id(test_certificates):
    """CONNECT ストリームのリセットでセッション ID が渡ることを確認

    2 つ目のセッションの CONNECT ストリーム (最小 ID でない CONNECT) を
    クライアントがリセットすると、セッション ID (= CONNECT ストリーム ID。
    draft-ietf-webtrans-http3-16 Section 2.2) が on_stream_reset に渡る
    """
    server, server_task, info = await _start_reset_test_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

        _first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)

        # 2 つ目のセッションの CONNECT ストリームをリセットする
        await client.reset_stream(second_session_id)

        # セッション ID (= CONNECT ストリーム ID) が渡る
        await asyncio.wait_for(info.reset_received.wait(), timeout=5.0)
        assert info.reset_stream_id == second_session_id
        assert info.reset_session_id == second_session_id
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_connect_stream_reset_notifies_session_closed(test_certificates):
    """CONNECT ストリームのリセットでセッション終了が通知されることを確認

    同一 QUIC 接続上に 2 セッションを確立し、1 つ目のセッションの CONNECT
    ストリームをクライアントがリセットすると、on_session_closed が正しい
    セッション ID で 1 回だけ発火し、2 つ目のセッションのデータ送受信が
    継続できることを確認する (draft-ietf-webtrans-http3-16 Section 6 の
    セッション終了条件の 1 つ目)。旧実装では CONNECT ストリームのリセットで
    セッション ID が session_ids_ に残り続け、on_session_closed が発火
    しなかった
    """
    server, server_task, info = await _start_session_closed_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

        first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)
        assert info.session_ids == [first_session_id, second_session_id]

        # 1 つ目のセッションの CONNECT ストリームをリセットする
        await client.reset_stream(first_session_id, error_code=0x42)

        # on_session_closed が正しいセッション ID で 1 回だけ発火する
        await asyncio.wait_for(info.session_closed.wait(), timeout=5.0)
        assert info.closed_session_ids == [first_session_id]

        # クライアント側の SessionClosed イベントに error_code がローカル伝播する
        # (QUIC STREAM_RESET のアプリエラーコード)。1 回だけ発火することも
        # 確認する (サーバー側の closed_session_ids と対称)
        client_session_closed = None
        client_session_closed_count = 0
        while True:
            event = client._h3_session.next_event()
            if event is None:
                break
            if event.type == h3_low.EventType.SESSION_CLOSED:
                client_session_closed = event
                client_session_closed_count += 1
        assert client_session_closed_count == 1
        assert client_session_closed is not None
        assert client_session_closed.session_id == first_session_id
        assert client_session_closed.error_code == 0x42

        # 終了したセッションが session_ids_ から削除される
        assert client._h3_session.get_session_ids() == [second_session_id]

        # 2 つ目のセッションのデータ送受信が継続できることを確認する
        stream_id = await client.open_stream(second_session_id)
        await client.send_stream_data(stream_id, b"still-alive")

        await asyncio.wait_for(info.data_received.wait(), timeout=5.0)
        assert info.data_session_id == second_session_id
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_connect_stream_fin_notifies_session_closed(test_certificates):
    """CONNECT ストリームの FIN でセッション終了が通知されることを確認

    同一 QUIC 接続上に 2 セッションを確立し、1 つ目のセッションの CONNECT
    ストリームを空 FIN でクリーンクローズすると、on_session_closed が正しい
    セッション ID で 1 回だけ発火し、2 つ目のセッションのデータ送受信が
    継続できることを確認する (draft-ietf-webtrans-http3-16 Section 6 の
    セッション終了条件の 1 つ目)。旧実装では end_stream コールバックを
    登録しておらず、CONNECT ストリームの FIN でセッション ID が
    session_ids_ に残り続け、on_session_closed が発火しなかった
    """
    server, server_task, info = await _start_session_closed_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

        first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)
        assert info.session_ids == [first_session_id, second_session_id]

        # 1 つ目のセッションの CONNECT ストリームに空 FIN を直接注入して
        # 届ける (高レベル API には CONNECT ストリームへ FIN を送出する
        # 手段が無いため)
        client._quic_connection.send_stream_data(first_session_id, b"", fin=True)
        await client._send_quic_only()

        # on_session_closed が正しいセッション ID で 1 回だけ発火する
        await asyncio.wait_for(info.session_closed.wait(), timeout=5.0)
        assert info.closed_session_ids == [first_session_id]

        # サーバーからの応答 FIN を受信して、クライアント側の SessionClosed
        # イベントが発火するまで待つ (最大 5 秒。受信ループは _receive の
        # 0.1 秒タイムアウトで駆動する)。応答 FIN は server.py の
        # SESSION_CLOSED ハンドラによる QUIC 直接注入の 1 経路で届く。
        # error_code は 0 (クリーンクローズ。WT_CLOSE_SESSION 無しの FIN は
        # error code 0 かつ空のエラー文字列の WT_CLOSE_SESSION と等価。
        # draft-ietf-webtrans-http3-16 Section 6) で 1 回だけ発火すること
        # を確認する
        client_session_closed = None
        client_session_closed_count = 0
        deadline = asyncio.get_running_loop().time() + 5.0
        while client_session_closed is None:
            await client._receive()
            if not client._process_quic_events():
                break
            while True:
                event = client._h3_session.next_event()
                if event is None:
                    break
                if event.type == h3_low.EventType.SESSION_CLOSED:
                    client_session_closed = event
                    client_session_closed_count += 1
            if client_session_closed is None and asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.01)
        assert client_session_closed_count == 1
        assert client_session_closed is not None
        assert client_session_closed.session_id == first_session_id
        assert client_session_closed.error_code == 0

        # 終了したセッションが session_ids_ から削除される
        assert client._h3_session.get_session_ids() == [second_session_id]

        # 2 つ目のセッションのデータ送受信が継続できることを確認する
        stream_id = await client.open_stream(second_session_id)
        await client.send_stream_data(stream_id, b"still-alive")

        await asyncio.wait_for(info.data_received.wait(), timeout=5.0)
        assert info.data_session_id == second_session_id
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_server_resets_client_connect_stream_closes_session(test_certificates):
    """サーバーがクライアントの CONNECT ストリームをリセットするとクライアントのセッションが終了することを確認

    高レベル Server が server.reset_stream でクライアントの CONNECT ストリーム
    (セッション ID) をリセットすると、クライアント側で SessionClosed が発火して
    is_connected が False になることを確認する。旧実装では CONNECT ストリームの
    リセットで SessionClosed が発火せず、is_connected が True のまま残っていた
    """
    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()
    client_session_closed_event = asyncio.Event()
    client_addr = None
    client_session_id = None

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        nonlocal client_addr, client_session_id
        client_addr = addr
        client_session_id = session_id
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
    )

    async def on_client_session_closed(session_id):
        client_session_closed_event.set()

    client.on_session_closed(on_client_session_closed)

    connected = await client.connect()
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    try:
        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
        assert client_addr is not None
        assert client_session_id is not None

        # サーバーがクライアントの CONNECT ストリームをリセットする
        await server.reset_stream(client_addr, client_session_id, error_code=0x03)

        # クライアント側で SessionClosed が発火して切断状態になる
        await asyncio.wait_for(client_session_closed_event.wait(), timeout=5.0)
        assert client.is_connected is False
    finally:
        client_task.cancel()
        server_task.cancel()
        await asyncio.gather(client_task, server_task, return_exceptions=True)

        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_server_fin_closes_client_session(test_certificates):
    """サーバーが CONNECT ストリームへ空 FIN を送出するとクライアントのセッションが終了することを確認

    高レベル Server が CONNECT ストリーム (セッション ID) へ空 FIN を
    送出すると、クライアント側で SessionClosed が発火して is_connected が
    False になることを確認する。高レベル API には CONNECT ストリームへ FIN
    を送出する手段が無いため、サーバー内部の quic_connection への直接注入で
    空 FIN を届ける。クライアントはリセットではなく FIN (クリーンクローズ)
    でセッション終了を検知する。旧実装では FIN 経路のセッション終了検知が
    無く、 SessionClosed が発火せず is_connected が True のまま残っていた
    """
    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()
    client_session_closed_event = asyncio.Event()
    client_addr = None
    client_session_id = None

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        nonlocal client_addr, client_session_id
        client_addr = addr
        client_session_id = session_id
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
    )

    async def on_client_session_closed(session_id):
        client_session_closed_event.set()

    client.on_session_closed(on_client_session_closed)

    connected = await client.connect()
    assert connected is True

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    try:
        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
        assert client_addr is not None
        assert client_session_id is not None

        # サーバーが CONNECT ストリームへ空 FIN を直接注入して送出する
        # (高レベル API には CONNECT ストリームへ FIN を送出する手段が無い)
        server_client = server._clients[client_addr]
        server_client.quic_connection.send_stream_data(
            client_session_id,
            b"",
            fin=True,
        )
        await server._send_to(client_addr, server_client)

        # クライアント側で SessionClosed が発火して切断状態になる
        await asyncio.wait_for(client_session_closed_event.wait(), timeout=5.0)
        assert client.is_connected is False
    finally:
        client_task.cancel()
        server_task.cancel()
        await asyncio.gather(client_task, server_task, return_exceptions=True)

        await client.close()
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quarter_stream_id",
    [1 << 60, 1 << 61],
    ids=["2^60_positive_overflow", "2^61_negative"],
)
async def test_datagram_invalid_session_id_closes_connection(
    test_certificates,
    quarter_stream_id,
):
    """巨大な Quarter Stream ID を持つデータグラムでサーバーが接続を閉じることを確認する

    仕様逸脱ピアが巨大な Quarter Stream ID を持つデータグラムを送った場合、
    サーバーは H3_ID_ERROR (0x0108) で接続を閉じる (draft-ietf-webtrans-http3-16
    Section 4 の MUST)。負のセッション ID になる 2^61 以上と、正のまま範囲超過に
    なる 2^60 以上 2^61 未満の両方を検証する。不正なセッション ID は on_datagram
    に渡らない。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    datagram_received = asyncio.Event()

    async def on_datagram(session_id: int, data: bytes, addr: tuple[str, int]) -> None:
        datagram_received.set()

    server.on_datagram(on_datagram)

    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = _LowLevelClient(server.actual_port)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
        session_id = await client.establish_session()
        assert session_id >= 0

        # 巨大な Quarter Stream ID を 8 バイト varint でエンコードする
        # (RFC 9000 可変長整数)。2^60 以上 2^61 未満は正のまま範囲超過、
        # 2^61 以上は int64 のラップで負のセッション ID になる
        varint = (0xC0 << 56 | quarter_stream_id).to_bytes(8, "big")
        client._quic_connection.send_datagram(varint + b"huge-quarter-stream-id")
        # send() はストリームデータの後にデータグラムを書き込む (残留データが
        # あると ngtcp2 の MORE 契約により同一パケットに同梱される) ため、
        # 通常は 1 回のフラッシュで届く。残留ストリームデータの掃き出しを
        # 確実にする防御として複数回フラッシュする
        for _ in range(8):
            await client._send_packet()

        # サーバーが H3_ID_ERROR で接続を閉じる。CONNECTION_CLOSE を受信して
        # error_code() が 0x0108 になるまで待つ
        connection_closed = False
        for _ in range(100):
            await client._receive()
            if not client._process_quic_events():
                connection_closed = True
                break
            await asyncio.sleep(0.01)
        assert connection_closed is True
        assert client._quic_connection.error_code == 0x0108
        # 不正なセッション ID のデータグラムは on_datagram に渡らない
        assert datagram_received.is_set() is False

        # エントリ削除後に同一アドレスから追従パケット (非 Initial) が届いても
        # サーバーは黙って破棄して run() を継続する。未対策だと accept が
        # RuntimeError を投げてサーバータスクが例外終了する (遠隔 DoS の入口)
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(
            client._socket,
            varint + b"huge-quarter-stream-id",
            ("127.0.0.1", server.actual_port),
        )
        await asyncio.sleep(0.05)
        assert server_task.done() is False
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quarter_stream_id",
    [1 << 60, 1 << 61],
    ids=["2^60_positive_overflow", "2^61_negative"],
)
async def test_datagram_invalid_session_id_closes_connection_client(
    test_certificates,
    quarter_stream_id,
):
    """不正なセッション ID のデータグラムでクライアントが接続を閉じることを確認する

    サーバーが巨大な Quarter Stream ID を持つデータグラムを送った場合、
    クライアントは H3_ID_ERROR (0x0108) で接続を閉じる
    (draft-ietf-webtrans-http3-16 Section 4 の MUST)。サーバー側の
    test_datagram_invalid_session_id_closes_connection と対をなす検証で、
    C++ の receive_datagram が Error イベントを生成し、高レベル Client の
    ERROR ハンドラが接続を閉じることを確認する。不正なセッション ID は
    on_datagram に渡らない。
    """
    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()
    client_addr = None

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        nonlocal client_addr
        client_addr = addr
        session_ready_event.set()

    server.on_session_ready(on_session_ready)
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    datagram_received = asyncio.Event()

    async def on_client_datagram(data: bytes) -> None:
        datagram_received.set()

    client.on_datagram(on_client_datagram)

    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    try:
        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
        assert client_addr is not None

        # サーバーが巨大な Quarter Stream ID を 8 バイト varint でエンコードした
        # データグラムを送る (RFC 9000 可変長整数)。2^60 以上 2^61 未満は
        # 正のまま範囲超過、2^61 以上は int64 のラップで負のセッション ID に
        # なる。h3 層の send_datagram は session_id を 4 で割って Quarter
        # Stream ID を復元するが、引数が int64 のため 2^61 相当のセッション ID
        # (2^63) は収まらず送れない。そこで QUIC 層にワイヤ形式のデータグラムを
        # 直接注入する
        varint = (0xC0 << 56 | quarter_stream_id).to_bytes(8, "big")
        server_client = server._clients[client_addr]
        assert server_client.quic_connection is not None
        server_client.quic_connection.send_datagram(varint + b"huge-quarter-stream-id")
        await server._send_to(client_addr, server_client)

        # クライアントが H3_ID_ERROR で接続を閉じる。ERROR ハンドラが
        # _running を False にするため run() が終了する
        await asyncio.wait_for(client_task, timeout=5.0)
        assert client.is_connected is False
        # 不正なセッション ID のデータグラムは on_datagram に渡らない
        assert datagram_received.is_set() is False

        # サーバーがクライアントからの CONNECTION_CLOSE (error_code 0x0108) を
        # 受信するまで待つ。サーバーは受信後に _clients からエントリを削除するが、
        # error_code は ngtcp2 の ccerr を参照するため、テスト側で保持した
        # server_client から削除後も取得できる
        error_code_observed = False
        for _ in range(100):
            if server_client.quic_connection.error_code == 0x0108:
                error_code_observed = True
                break
            await asyncio.sleep(0.01)
        assert error_code_observed is True
    finally:
        client_task.cancel()
        server_task.cancel()
        await asyncio.gather(client_task, server_task, return_exceptions=True)

        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_datagram_closed_session_id_discarded(test_certificates):
    """閉じたセッションの ID 宛てのデータグラムが破棄されることを確認

    終了したセッション ID 宛のデータグラムはアプリに配信されない
    (実装ポリシー。draft-ietf-webtrans-http3-16 Section 4 の「closed session
    宛のデータの扱いは Section 6 に従う」と、データグラムは再送されず配信
    保証がないこと (Section 4.1 / RFC 9221) が根拠)。セッション ID の構造
    検証 (範囲外 ID の H3_ID_ERROR) は維持され、閉じたセッションの ID を
    含む正常なセッション ID のデータグラムで接続が閉じないことを併せて
    確認する。
    """
    server, server_task, info = await _start_session_closed_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

        first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)
        assert info.session_ids == [first_session_id, second_session_id]

        # サーバー側のクライアントアドレスを取得する (接続は 1 つだけ)
        (client_addr,) = server._clients.keys()

        # 1 つ目のセッションを WT_CLOSE_SESSION で閉じる
        client._h3_session.close_session(first_session_id)
        await client._pump()

        # サーバー側のセッション終了を待つ
        await asyncio.wait_for(info.session_closed.wait(), timeout=5.0)
        assert info.closed_session_ids == [first_session_id]

        # クライアント側でもセッションが閉じたことを確認する
        assert client._h3_session.get_session_ids() == [second_session_id]

        # 閉じたセッションの ID 宛てのデータグラムは破棄される
        # (受信側の検証。送信側の高レベル send_datagram は終了した
        # セッションへの送信を無視するため、QUIC 層へのワイヤ形式
        # 直接注入で検証する)
        server_client = server._clients[client_addr]
        assert server_client.quic_connection is not None
        wire_datagram = _encode_wt_datagram(first_session_id, b"closed-dg")
        server_client.quic_connection.send_datagram(wire_datagram)
        # send() はストリームデータの後にデータグラムを書き込むため、
        # 残留ストリームデータがあるとデータグラムが次回のパケットに
        # 回り得る。確実に送出するため複数回フラッシュする
        for _ in range(8):
            await server._send_to(client_addr, server_client)
        # 破棄されるため、タイムアウトしても受信しない
        datagram_event = await client._receive_datagram()
        assert datagram_event is None

        # 構造検証は維持され、閉じたセッションの ID のデータグラムで
        # 接続が閉じない
        assert client._h3_session.is_closed() is False
        assert server_client.quic_connection.is_closed() is False

        # 開いているセッションの ID 宛てのデータグラムは従来どおり配送される
        await server.send_datagram(client_addr, second_session_id, b"open-dg")
        datagram_event = await client._receive_datagram()
        assert datagram_event is not None
        assert datagram_event.session_id == second_session_id
        assert datagram_event.data == b"open-dg"
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_server_stop_delivers_connection_close(test_certificates):
    """サーバー stop() が CONNECTION_CLOSE を送出してクライアントが終了を検知する"""
    from webtransport.h3 import Client, Server

    session_ready_event = asyncio.Event()
    client_finished_event = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready_event.set()

    server.on_session_ready(on_session_ready)

    server_task = None
    client_task = None
    client = None
    try:
        await server.start()

        client = Client(
            url=f"https://127.0.0.1:{server.actual_port}/webtransport",
            verify_peer=False,
        )

        async def run_server():
            try:
                await server.run()
            except asyncio.CancelledError:
                pass

        server_task = asyncio.create_task(run_server())

        connected = await client.connect()
        assert connected is True

        async def run_client():
            try:
                await client.run()
                # run() が自然終了した (CancelledError ではない) 場合のみ到達する。
                # stop() の CONNECTION_CLOSE を受信して run() が終了した証拠
                client_finished_event.set()
            except asyncio.CancelledError:
                pass

        client_task = asyncio.create_task(run_client())

        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)

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
