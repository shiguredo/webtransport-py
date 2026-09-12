"""webtransport.h3 (WebTransport over HTTP/3) テスト

高レベル API (Client / Server) のテスト。同一 QUIC 接続上に複数セッションを
確立する低レベル API のテストは test_e2e_webtransport_h3_low_level.py に置く。
"""

import asyncio
import time
from typing import Literal

import pytest

from webtransport import quic
from webtransport.exceptions import (
    ConnectRefusedError,
    ConnectTimeoutError,
    HandshakeFailedError,
)
from webtransport.h3 import Server


def test_import_server_client():
    """Server と Client がインポートできることを確認"""
    from webtransport.h3 import Client

    assert Server is not None
    assert Client is not None


def test_import_all():
    """全ての公開 API がインポートできることを確認"""
    from webtransport.h3 import (
        Client,
        Config,
        Event,
        EventType,
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

    server = Server(host="127.0.0.1", port=0)
    assert server.host == "127.0.0.1"
    assert server.port == 0
    assert server.actual_port == 0
    assert server.is_running is False


def test_server_init_with_options():
    """Server がオプション付きで初期化できることを確認"""

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

    server = Server(host="127.0.0.1", port=0)
    await server.start()
    assert server.is_running is True
    assert server.actual_port > 0

    await server.stop()
    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_context_manager():
    """Server がコンテキストマネージャーとして使えることを確認"""

    async with Server(host="127.0.0.1", port=0) as server:
        assert server.is_running is True
        assert server.actual_port > 0

    assert server.is_running is False


@pytest.mark.asyncio
async def test_server_multiple_start_stop():
    """Server の複数回 start/stop ができることを確認"""

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    発火しない)。クライアントの connect() は 403 拒否を検知して
    HandshakeFailedError を送出する
    (低レベルの SessionRejected イベント、status_code 付き)。
    """
    from webtransport.h3 import Client

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

    # QUIC トランスポートの接続は成功するが、CONNECT リクエスト自体は拒否
    # (403) されるため、connect() は HandshakeFailedError を送出する
    # (draft-16 Section 3.2)
    with pytest.raises(HandshakeFailedError):
        await client.connect()
    assert client.is_connected is False

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_origin_verification_accepts_without_origin(test_certificates):
    """allowed_origins 設定時でも Origin ヘッダー無しの接続は受理されることを確認する

    仕様上 Origin ヘッダーは非ブラウザクライアントでは OPTIONAL であり、
    Origin ヘッダーが無いリクエストは従来どおり受理する。
    """
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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

        # 再開放が機能しないと echo が完了せずタイムアウトする。
        # CI の pytest-timeout (30 秒) で先に殺されないよう、実測 (ローカルで
        # 約 8 秒) に余裕を足した 10 秒を上限にする
        await asyncio.wait_for(echo_completed.wait(), timeout=10.0)

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()
    client_session_id = client.session_id

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)

    # クライアントがセッションを閉じる (run() 並行時も待機する)
    await client.close()
    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)

    # run() 並行時もピア終了を観測する
    assert client._close_wait_result == "peer-closed"

    await asyncio.wait_for(session_closed_event.wait(), timeout=5.0)

    assert closed_session_ids == [client_session_id]

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await server.stop()


@pytest.mark.asyncio
async def test_server_resets_client_stream(test_certificates):
    """Server がアプリコードで reset_stream すると Client の on_stream_reset が受信側で復元した値を受け取ることを確認"""
    from webtransport.h3 import Client

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

    await client.connect()

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
    # 送信側がアプリコード 0x01 を WT_APPLICATION_ERROR へリマップし、
    # 受信側はアプリコードへ復元して配信する (Section 4.4)
    assert reset_info["error_code"] == 0x01

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_client_resets_server_stream(test_certificates):
    """Client がアプリコードで reset_stream すると Server の on_stream_reset が受信側で復元した値を受け取ることを確認"""
    from webtransport.h3 import Client

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

    await client.connect()

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
    # 送信側がアプリコード 0x02 を WT_APPLICATION_ERROR へリマップし、
    # 受信側はアプリコードへ復元して配信する (Section 4.4)
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
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    # クライアントは接続を維持する。内部解放のワイヤコード 0 は
    # WT_APPLICATION_ERROR レンジ外のためアプリには None で配信される
    await asyncio.wait_for(client_reset_received.wait(), timeout=5.0)
    assert len(client_resets) == 1
    assert client_resets[0][1] is None

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
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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


@pytest.mark.asyncio
async def test_server_resets_client_connect_stream_closes_session(test_certificates):
    """サーバーがクライアントの CONNECT ストリームをリセットするとクライアントのセッションが終了することを確認

    高レベル Server が server.reset_stream でクライアントの CONNECT ストリーム
    (セッション ID) をリセットすると、クライアント側で SessionClosed が発火して
    is_connected が False になることを確認する。旧実装では CONNECT ストリームの
    リセットで SessionClosed が発火せず、is_connected が True のまま残っていた
    """
    from webtransport.h3 import Client

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

    await client.connect()

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
    from webtransport.h3 import Client

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

    await client.connect()

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
async def test_datagram_invalid_session_id_closes_connection_client(
    test_certificates,
    quarter_stream_id,
):
    """不正なセッション ID のデータグラムでクライアントが接続を閉じることを確認する

    サーバーが巨大な Quarter Stream ID を持つデータグラムを送った場合、
    クライアントは H3_ID_ERROR (0x0108) で接続を閉じる
    (draft-ietf-webtrans-http3-16 Section 4 の MUST)。サーバー側の
    test_datagram_invalid_session_id_closes_connection
    (test_e2e_webtransport_h3_low_level.py) と対をなす検証で、
    C++ の receive_datagram が Error イベントを生成し、高レベル Client の
    ERROR ハンドラが接続を閉じることを確認する。不正なセッション ID は
    on_datagram に渡らない。
    """
    from webtransport.h3 import Client

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

    await asyncio.wait_for(client.connect(), timeout=5.0)

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
async def test_server_stop_delivers_connection_close(test_certificates):
    """サーバー stop() が CONNECTION_CLOSE を送出してクライアントが終了を検知する"""
    from webtransport.h3 import Client

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

        await client.connect()

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


@pytest.mark.asyncio
async def test_client_open_stream_after_session_close_returns_minus_one(test_certificates):
    """セッション終了後の Client.open_stream が -1 を返し RESET_STREAM を送出することを確認

    セッション終了後に open_stream を呼ぶと、h3 層の登録が失敗するため
    QUIC ストリームだけが開いた無効な stream_id が返っていた問題の修正。
    修正後は -1 を返し、開いた QUIC ストリームを RESET_STREAM で解放する
    (Server.open_stream と対称の挙動)。RESET_STREAM はサーバー側の
    on_stream_reset で観測する (WT ヘッダー未受信のため session_id は -1)。
    内部解放リセットのワイヤコード 0 は WT_APPLICATION_ERROR レンジ外のため、
    アプリにはエラーコードなし (None) として配信される
    (draft-ietf-webtrans-http3-16 Section 4.4)。
    """
    from webtransport.h3 import Client

    client_addr = None
    client_session_id = None
    session_ready_event = asyncio.Event()
    client_session_closed_event = asyncio.Event()
    server_stream_reset_event = asyncio.Event()
    server_resets = []

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

    async def on_stream_reset(session_id, stream_id, error_code, addr):
        server_resets.append((session_id, error_code))
        if error_code is None:
            server_stream_reset_event.set()

    server.on_stream_reset(on_stream_reset)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    client_task = None

    async def on_session_closed(session_id):
        client_session_closed_event.set()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    client.on_session_closed(on_session_closed)

    try:
        await client.connect()

        async def run_client():
            try:
                await client.run()
            except asyncio.CancelledError:
                pass

        client_task = asyncio.create_task(run_client())

        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
        assert client_addr is not None
        assert client_session_id is not None

        # サーバー側の低レベルセッションを閉じ、クライアントに WT_CLOSE_SESSION
        # を送ってセッション終了を学習させる
        server_client = server._clients[client_addr]
        assert server_client.webtransport_session is not None
        server_client.webtransport_session.close_session(client_session_id, 0)
        # send() は 1 パケットに留める設計のため、残留データの掃き出しを
        # 確実にする防御として複数回フラッシュする
        for _ in range(8):
            await server._send_to(client_addr, server_client)

        await asyncio.wait_for(client_session_closed_event.wait(), timeout=5.0)

        failed_stream_id = await client.open_stream()
        assert failed_stream_id == -1

        # 開いた QUIC ストリームの RESET_STREAM (ワイヤ 0 → 配信は None) が届く
        await asyncio.wait_for(server_stream_reset_event.wait(), timeout=5.0)
        none_code_resets = [r for r in server_resets if r[1] is None]
        assert len(none_code_resets) == 1
        assert none_code_resets[0][0] == -1
    finally:
        if client_task is not None:
            client_task.cancel()
        server_task.cancel()
        await asyncio.gather(
            *(t for t in [client_task, server_task] if t is not None),
            return_exceptions=True,
        )

        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_client_open_stream_before_connect_returns_minus_one():
    """connect 前 (未確立) の Client.open_stream が -1 を返すことを確認

    接続が確立されていない状態では既存ガード (_quic_connection が None) で
    -1 を返す。回帰確認。
    """
    from webtransport.h3 import Client

    client = Client(
        url="https://127.0.0.1:4433/webtransport",
        verify_peer=False,
    )

    stream_id = await client.open_stream()
    assert stream_id == -1


def _make_config_missing_transport_params(
    missing: Literal["datagram", "reset_stream_at", "both"],
) -> quic.Config:
    """指定した transport parameter を欠落させる QUIC 設定を作る

    draft-ietf-webtrans-http3-16 Section 3.1 の MUST (max_datagram_frame_size
    > 0 と reset_stream_at の送信) を満たさないピアを実スタックで作るための
    テスト用設定。
    """
    config = quic.Config()
    if missing in ("datagram", "both"):
        config.enable_datagram = False
    if missing in ("reset_stream_at", "both"):
        config.enable_reset_stream_at = False
    return config


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing",
    ["datagram", "both"],
    ids=["datagram", "both"],
)
async def test_client_connect_rejects_server_without_transport_params(
    test_certificates,
    missing,
):
    """サーバーが transport parameter を欠落させていると Client.connect() が HandshakeFailedError を送出することを確認

    draft-ietf-webtrans-http3-16 Section 3.1 の MUST (サーバーは
    max_datagram_frame_size > 0 を送ること) を満たさないサーバーとは
    セッションを確立しない。検証はハンドシェイク完了直後 (CONNECT 送出前)
    に行われるため、クライアントは CONNECT を送らずに HandshakeFailedError
    を送出する。サーバー側で SESSION_READY が発火しないことをあわせて検証し、
    CONNECT が送出されていないことを直接確認する。修正前は検証が
    無く、要件未達のサーバーとセッションが確立し得た。
    reset_stream_at の欠落は必須としない (実ブラウザ互換) ため、
    このテストの対象外である。
    """
    from webtransport.h3 import Client

    server_session_ready_called = False

    async def on_session_ready(session_id, addr):
        nonlocal server_session_ready_called
        server_session_ready_called = True

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        quic_config=_make_config_missing_transport_params(missing),
    )
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

    try:
        with pytest.raises(HandshakeFailedError):
            await client.connect()
        assert client.is_connected is False
        # CONNECT が送出されていないため、サーバー側でセッション要求は来ない
        assert server_session_ready_called is False
    finally:
        await client.close()
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing",
    ["datagram", "both"],
    ids=["datagram", "both"],
)
async def test_server_rejects_client_without_transport_params(
    test_certificates,
    missing,
):
    """クライアントが transport parameter を欠落させているとサーバーが接続を閉じることを確認

    draft-ietf-webtrans-http3-16 Section 3.1 の MUST を満たさないクライアント
    に対し、サーバーは確立済み・新規の全セッションを malformed として扱い、
    H3_MESSAGE_ERROR (RFC 9114 Section 8.1) で接続を閉じる。connect() は
    2xx 応答を待つため、拒否された場合は ConnectRefusedError を送出する。
    例外送出と on_session_ready 不発火で検証する。
    reset_stream_at の欠落は必須としない (実ブラウザ互換) ため、
    このテストの対象外である。
    """
    from webtransport.h3 import Client

    server_session_ready_called = False

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        nonlocal server_session_ready_called
        server_session_ready_called = True

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
        quic_config=_make_config_missing_transport_params(missing),
    )

    try:
        # 要件未達のクライアントの CONNECT はサーバーが H3_MESSAGE_ERROR
        # で拒否する。connect() は応答待ちで ConnectRefusedError を送出す
        # ることが期待される (draft-16 Section 3.2。ハンドシェイク完了後の
        # CONNECTION_CLOSED 受信による拒否)
        with pytest.raises(ConnectRefusedError):
            await asyncio.wait_for(client.connect(), timeout=5.0)
        assert client.is_connected is False
        # 要件未達のクライアントのセッションは確立されない
        assert server_session_ready_called is False
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_client_connect_accepts_server_without_reset_stream_at(
    test_certificates,
):
    """reset_stream_at を送らないサーバーでも Client.connect() が成功することを確認

    draft-ietf-webtrans-http3-16 Section 3.1 はクライアント・サーバー双方に
    reset_stream_at の送信を要求するが、実ブラウザ (Chromium / WebKit) が
    現時点で送信しないため、相互運用性を優先して必須としない (緩和)。
    緩和前はブラウザ E2E テストが全滅した (サーバー側が require している
    ため)。reset_stream_at を欠落させたサーバーとセッションが確立できる
    ことを検証する。max_datagram_frame_size > 0 の欠落は引き続き拒否される。
    """
    from webtransport.h3 import Client

    server_session_ready_called = False

    async def on_session_ready(session_id, addr):
        nonlocal server_session_ready_called
        server_session_ready_called = True

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        quic_config=_make_config_missing_transport_params("reset_stream_at"),
    )
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

    client_task = None
    try:
        await client.connect()
        assert client.is_connected is True

        async def run_client():
            await client.run()

        client_task = asyncio.create_task(run_client())

        # セッション要求がサーバーに届くまで待ってから検証する。
        # connect() の返却は楽観的 (2xx 応答を待たない) ため、
        # サーバー側の CONNECT 処理完了を保証しない
        deadline = time.monotonic() + 5.0
        while not server_session_ready_called and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert server_session_ready_called is True
    finally:
        if client_task is not None:
            client_task.cancel()
        server_task.cancel()
        await asyncio.gather(
            *(t for t in [client_task, server_task] if t is not None),
            return_exceptions=True,
        )
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_server_accepts_client_without_reset_stream_at(
    test_certificates,
):
    """reset_stream_at を送らないクライアントでもサーバーが接続を維持することを確認

    draft-ietf-webtrans-http3-16 Section 3.1 はクライアント・サーバー双方に
    reset_stream_at の送信を要求するが、実ブラウザ (Chromium / WebKit) が
    現時点で送信しないため、相互運用性を優先して必須としない (緩和)。
    緩和前は SESSION_READY 時にサーバーが H3_MESSAGE_ERROR (0x010E) で
    接続を閉じ、実ブラウザからのセッション確立が全滅した。ここでは
    reset_stream_at を欠落させたクライアントからセッションが確立される
    ことを検証する。max_datagram_frame_size > 0 の欠落は引き続き拒否される。
    """
    from webtransport.h3 import Client

    server_session_ready_called = False

    async def on_session_ready(session_id, addr):
        nonlocal server_session_ready_called
        server_session_ready_called = True

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
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
        quic_config=_make_config_missing_transport_params("reset_stream_at"),
    )

    client_task = None
    try:
        await asyncio.wait_for(client.connect(), timeout=5.0)

        async def run_client():
            await client.run()

        client_task = asyncio.create_task(run_client())

        # 接続が 0x010E で閉じられていないことを確認するため、
        # run() が即終了せず継続していることが期待される
        await asyncio.sleep(0.5)
        assert client_task.done() is False
        assert server_session_ready_called is True
    finally:
        if client_task is not None:
            client_task.cancel()
        server_task.cancel()
        await asyncio.gather(
            *(t for t in [client_task, server_task] if t is not None),
            return_exceptions=True,
        )
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_connect_timeout_on_blackhole():
    """応答が返らない UDP 宛先に対して connect() が ConnectTimeoutError を送出することを確認する

    前提: blackhole IP (10.255.255.1) は issue の完了条件が例示する
    ルーティングされるが応答を返さないアドレスである (経路の無い環境では
    到達不能系の例外になり得る)。
    期待値: timeout=1.0 で 1 秒強で ConnectTimeoutError が送出される。
    """
    from webtransport.h3 import Client

    # blackhole 宛てのクライアントを構築する
    client = Client(
        url="https://10.255.255.1:443/webtransport",
        verify_peer=False,
    )
    try:
        # 計測区間の開始
        start = time.monotonic()
        with pytest.raises(ConnectTimeoutError):
            await client.connect(timeout=1.0)
        elapsed = time.monotonic() - start
        # deadline 到達で打ち切られるため、1 秒強で復帰する
        assert elapsed >= 0.9
        assert elapsed < 10.0
        assert client.is_connected is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_idle_timeout_reaps_connection(test_certificates):
    """アイドルタイムアウトで接続が回収されセッション終了は一斉発火しない"""
    from webtransport.h3 import Client

    closed_sessions: list[int] = []

    async def on_session_closed(session_id: int, addr: tuple[str, int]) -> None:
        closed_sessions.append(session_id)

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        idle_timeout_ns=1_000_000_000,
    )
    server.on_session_closed(on_session_closed)
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    try:
        # セッションを確立して沈黙させる
        client = Client(
            url=f"https://127.0.0.1:{server.actual_port}/webtransport",
            verify_peer=False,
        )
        await client.connect()

        async def run_client() -> None:
            try:
                await client.run()
            except asyncio.CancelledError:
                pass

        client_task = asyncio.create_task(run_client())
        try:
            # 接続されるまで待ってから検証する
            deadline = time.monotonic() + 5.0
            while len(server._clients) != 1 and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert len(server._clients) == 1

            # アイドルタイムアウト後に登録が外れる。削除されるまで待って
            # から検証する (CI 負荷のばらつきで偽失敗しないため)
            deadline = time.monotonic() + 5.0
            while server._clients != {} and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert server._clients == {}
            # 確立中セッションへの一斉通知は行わない
            assert closed_sessions == []
        finally:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
            await client.close()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_close_waits_for_peer_fin(test_certificates):
    """close() がピア終了を待ってから CONNECTION_CLOSE を送ることを確認

    通常経路ではサーバーが WT_CLOSE_SESSION 応答の FIN を返すため、
    待機結果が peer-closed になる。サーバー側の SESSION_CLOSED 到達は
    WT が CC より先に届いたことの証拠である (CC が先なら接続が閉じて
    WT は処理されないため、ワイヤ順序 WT → FIN → CC が成り立つ)。
    既定の待機上限が 3 秒であることも確認する。
    """
    from webtransport.h3 import Client

    # 既定の待機上限は 3 秒である
    assert Client(url="https://127.0.0.1:4433/webtransport")._close_wait_timeout == 3.0

    session_closed_event = asyncio.Event()

    async def on_session_closed(session_id, addr):
        session_closed_event.set()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
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
    await client.connect()
    try:
        # ピア終了を待って閉じる
        await client.close()
        # 待機結果はピア終了観測である
        assert client._close_wait_result == "peer-closed"
        # WT_CLOSE_SESSION がサーバーに届いている
        await asyncio.wait_for(session_closed_event.wait(), timeout=5.0)
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_close_times_out_without_peer_fin(test_certificates):
    """ピアが応答しなくても close() が上限で完了することを確認

    サーバー停止後の無応答ピアに対し、短い上限で待機が打ち切られ、
    待機結果が timeout になる。実時間で上限いっぱい待つことを確認する。
    """
    from webtransport.h3 import Client

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
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
        close_wait_timeout=0.5,
    )
    await client.connect()
    # サーバーを無応答にする (stop() は CONNECTION_CLOSE を送るため、
    # ソケットだけ閉じて応答も終了通知も来ない状態にする)
    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    if server._socket is not None:
        server._socket.close()
        server._socket = None
    try:
        # 上限いっぱい待って打ち切られる。上限超過の検出力のため下限を
        # 主眼とし、上限側は CI 変動の余裕を持たせる
        start = time.monotonic()
        await client.close()
        elapsed = time.monotonic() - start
        assert client._close_wait_result == "timeout"
        assert 0.4 <= elapsed < 2.0
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_close_skipped_without_wait(test_certificates):
    """上限 0 では待たずに閉じることを確認

    待機結果が skipped になり、WT_CLOSE_SESSION は送出されるため
    サーバー側にも SESSION_CLOSED が届く。
    """
    from webtransport.h3 import Client

    session_closed_event = asyncio.Event()

    async def on_session_closed(session_id, addr):
        session_closed_event.set()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
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
        close_wait_timeout=0,
    )
    await client.connect()
    try:
        # 待たずに閉じる
        await client.close()
        assert client._close_wait_result == "skipped"
        # WT_CLOSE_SESSION は送出されるためサーバーに届く
        await asyncio.wait_for(session_closed_event.wait(), timeout=5.0)
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_close_observes_peer_reset(test_certificates):
    """ピアのリセットも終了観測になることを確認"""
    from webtransport.h3 import Client

    session_ready_event = asyncio.Event()
    client_addr: list = []

    async def on_session_ready(session_id, addr):
        client_addr.append(addr)
        session_ready_event.set()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
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
    await client.connect()
    session_id = client.session_id
    try:
        await asyncio.wait_for(session_ready_event.wait(), timeout=5.0)
        # サーバーが CONNECT ストリームをリセットする
        await server.reset_stream(client_addr[0], session_id)
        # リセット観測で待機が終わる
        await client.close()
        assert client._close_wait_result == "peer-closed"
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_close_idempotent(test_certificates):
    """二重 close と未接続 close が安全であることを確認

    二重 close の 2 回目は待機せず結果を変えない。未接続 close は
    何もせず結果は none のままである。
    """
    from webtransport.h3 import Client

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
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    await client.connect()
    try:
        await client.close()
        first_result = client._close_wait_result
        assert first_result == "peer-closed"
        # 二重 close は待機せず結果を変えない
        await client.close()
        assert client._close_wait_result == first_result
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()

    # 未接続 close は何もせず結果は none のままである
    fresh = Client(url="https://127.0.0.1:4433/webtransport")
    await fresh.close()
    assert fresh._close_wait_result == "none"


@pytest.mark.asyncio
async def test_close_releases_socket_on_callback_error(test_certificates):
    """待機中のコールバック例外でも後始末して送出することを確認

    リセット通知のコールバックが例外を送出しても、QUIC クローズと
    ソケット破棄は行われてから例外が伝播する。
    """
    from webtransport.h3 import Client

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(session_id, stream_id, data, addr):
        # データストリームをリセットする
        await server.reset_stream(addr, stream_id)

    server.on_stream_data(on_server_stream_data)
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
        close_wait_timeout=2.0,
    )
    await client.connect()

    async def on_client_stream_reset(stream_id, error_code):
        raise RuntimeError("boom")

    client.on_stream_reset(on_client_stream_reset)
    stream_id = await client.open_stream()
    assert stream_id >= 0
    await client.send_stream_data(stream_id, b"hello")
    # リセットがクライアント受信バッファへ届くのを待つ
    await asyncio.sleep(0.3)
    try:
        with pytest.raises(RuntimeError, match="boom"):
            await client.close()
        # 後始末は終わっている (QUIC クローズとソケット破棄の両方)
        assert client._quic_connection is not None
        assert client._quic_connection.is_closed()
        assert client._socket is None
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()
