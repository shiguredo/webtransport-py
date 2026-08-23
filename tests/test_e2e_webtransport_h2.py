"""webtransport.h2 (WebTransport over HTTP/2) 高レベル API テスト"""

import asyncio
import ssl
import time
from collections.abc import Awaitable, Callable

import pytest

from webtransport.h2 import Server
from webtransport.webtransport_ext import h2 as h2_low


def _encode_h2_wt_stream_data_frame(http2_stream_id: int, wt_stream_id: int, data: bytes) -> bytes:
    """CONNECT ストリームへ載せる WT_STREAM (FIN なし) の DATA フレームを組み立てる"""
    from conftest import _encode_varint

    payload = _encode_varint(wt_stream_id) + data
    capsule = _encode_varint(0x190B4D3C) + _encode_varint(len(payload)) + payload
    return (
        len(capsule).to_bytes(3, "big")
        + bytes([0x00, 0x00])
        + (http2_stream_id & 0x7FFFFFFF).to_bytes(4, "big")
        + capsule
    )


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

    async def on_error(error_code, error_message, session_writer):
        pass

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)
    server.on_stream_data(on_stream_data)
    server.on_error(on_error)

    assert server._on_session_ready is not None
    assert server._on_session_closed is not None
    assert server._on_stream_data is not None
    assert server._on_error is not None


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

    async def on_error(error_code, error_message):
        pass

    client.on_session_ready(on_session_ready)
    client.on_session_closed(on_session_closed)
    client.on_stream_data(on_stream_data)
    client.on_error(on_error)

    assert client._on_session_ready is not None
    assert client._on_session_closed is not None
    assert client._on_stream_data is not None
    assert client._on_error is not None


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
    assert hasattr(EventType, "ERROR")


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


@pytest.mark.asyncio
async def test_recv_flow_control_violation_notifies_on_error(test_certificates):
    """受信フロー制御違反がサーバーの on_error に届くことを確認

    公開 API の send_stream_data は送信側クレジットで塞がれるため、クライアント
    の TLS ソケットへ WT_STREAM カプセルを直接書き込んで超過を再現する。
    サーバーのストリーム受信上限を 4 バイトにし、5 バイトを注入する。
    0x50 は WT_FLOW_CONTROL_ERROR (draft-15 Section 3.4 の 0xTBD) の
    プレースホルダ。draft で値が確定したら更新する。
    """
    from webtransport.h2 import Client, Config, Server

    error_codes: list[int] = []
    error_messages: list[str] = []
    stream_payloads: list[bytes] = []
    session_ready = asyncio.Event()
    error_received = asyncio.Event()

    config = Config()
    config.is_server = False
    config.wt_initial_max_stream_data = 4
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        config=config,
    )

    async def on_session_ready(session_writer) -> None:
        session_ready.set()

    async def on_stream_data(stream_id: int, data: bytes, session_writer) -> None:
        if data:
            stream_payloads.append(data)

    async def on_error(error_code: int, error_message: str, session_writer) -> None:
        error_codes.append(error_code)
        error_messages.append(error_message)
        error_received.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
    server.on_error(on_error)
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    assert await client.connect() is True
    await asyncio.wait_for(session_ready.wait(), timeout=5.0)

    assert client._writer is not None
    client._writer.write(_encode_h2_wt_stream_data_frame(client.session_id, 0, b"12345"))
    await client._writer.drain()

    await asyncio.wait_for(error_received.wait(), timeout=5.0)
    assert error_codes == [0x50]
    assert error_messages == ["peer exceeded flow control limit"]
    assert stream_payloads == []
    assert config.is_server is False
    assert config.wt_initial_max_stream_data == 4

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_client_recv_flow_control_violation_notifies_on_error(test_certificates):
    """受信フロー制御違反がクライアントの on_error に届くことを確認

    公開 API の send_stream_data は送信側クレジットで塞がれるため、サーバー
    の TLS ソケットへ WT_STREAM カプセルを直接書き込んで超過を再現する。
    クライアントのストリーム受信上限を 4 バイトにし、5 バイトを注入する。
    0x50 は WT_FLOW_CONTROL_ERROR (draft-15 Section 3.4 の 0xTBD) の
    プレースホルダ。draft で値が確定したら更新する。
    """
    from webtransport.h2 import Client, Config, Server, SessionWriter

    error_codes: list[int] = []
    error_messages: list[str] = []
    stream_payloads: list[bytes] = []
    session_writers: list[SessionWriter] = []
    session_ready = asyncio.Event()
    error_received = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_writer: SessionWriter) -> None:
        session_writers.append(session_writer)
        session_ready.set()

    server.on_session_ready(on_session_ready)
    await server.start()

    client_config = Config()
    client_config.wt_initial_max_stream_data = 4
    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
        config=client_config,
    )

    async def on_stream_data(stream_id: int, data: bytes) -> None:
        if data:
            stream_payloads.append(data)

    async def on_error(error_code: int, error_message: str) -> None:
        error_codes.append(error_code)
        error_messages.append(error_message)
        error_received.set()

    client.on_stream_data(on_stream_data)
    client.on_error(on_error)
    assert await client.connect() is True
    await asyncio.wait_for(session_ready.wait(), timeout=5.0)

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    session_writers[0]._writer.write(
        _encode_h2_wt_stream_data_frame(session_writers[0].session_id, 0, b"12345")
    )
    await session_writers[0]._writer.drain()

    await asyncio.wait_for(error_received.wait(), timeout=5.0)
    assert error_codes == [0x50]
    assert error_messages == ["peer exceeded flow control limit"]
    assert stream_payloads == []
    assert client_config.is_server is False
    assert client_config.wt_initial_max_stream_data == 4

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_stream_state_error_does_not_notify_on_error(test_certificates):
    """WT_STREAM_STATE_ERROR (0x51) はサーバーの on_error に届かないことを確認

    FIN 後の終端ストリームへデータ付き WT_STREAM を注入すると C++ は
    Error 0x51 を push してセッションを閉じる。高レベルは 0x50 のみを
    on_error に渡すため、コールバックは発火しない。クライアントが
    WT_CLOSE_SESSION を受けてセッション終了することをもって 0x51 経路を
    確認する。
    """
    from webtransport.h2 import Client, Server

    error_codes: list[int] = []
    stream_ready = asyncio.Event()
    session_closed = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_stream_data(stream_id: int, data: bytes, session_writer) -> None:
        if data:
            stream_ready.set()

    async def on_error(error_code: int, error_message: str, session_writer) -> None:
        error_codes.append(error_code)

    server.on_stream_data(on_stream_data)
    server.on_error(on_error)
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_session_closed(session_id: int) -> None:
        session_closed.set()

    client.on_session_closed(on_session_closed)
    assert await client.connect() is True

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"fin", fin=True)
    await asyncio.wait_for(stream_ready.wait(), timeout=5.0)

    assert client._writer is not None
    client._writer.write(_encode_h2_wt_stream_data_frame(client.session_id, stream_id, b"x"))
    await client._writer.drain()

    await asyncio.wait_for(session_closed.wait(), timeout=5.0)
    assert error_codes == []

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_client_stream_state_error_does_not_notify_on_error(test_certificates):
    """WT_STREAM_STATE_ERROR (0x51) はクライアントの on_error に届かないことを確認

    サーバーが FIN 後の終端ストリームへデータ付き WT_STREAM を注入する。
    クライアントは 0x51 でセッションを閉じるが on_error は発火しない。
    サーバーが WT_CLOSE_SESSION を受けてセッション終了することをもって
    0x51 経路を確認する。
    """
    from webtransport.h2 import Client, Server, SessionWriter

    error_codes: list[int] = []
    session_writers: list[SessionWriter] = []
    session_ready = asyncio.Event()
    stream_ready = asyncio.Event()
    session_closed = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_writer: SessionWriter) -> None:
        session_writers.append(session_writer)
        session_ready.set()

    async def on_session_closed(session_writer: SessionWriter) -> None:
        session_closed.set()

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_stream_data(stream_id: int, data: bytes) -> None:
        if data:
            stream_ready.set()

    async def on_error(error_code: int, error_message: str) -> None:
        error_codes.append(error_code)

    client.on_stream_data(on_stream_data)
    client.on_error(on_error)
    assert await client.connect() is True
    await asyncio.wait_for(session_ready.wait(), timeout=5.0)

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    stream_id = await session_writers[0].open_stream()
    await session_writers[0].send_stream_data(stream_id, b"fin", fin=True)
    await asyncio.wait_for(stream_ready.wait(), timeout=5.0)

    session_writers[0]._writer.write(
        _encode_h2_wt_stream_data_frame(session_writers[0].session_id, stream_id, b"x")
    )
    await session_writers[0]._writer.drain()

    await asyncio.wait_for(session_closed.wait(), timeout=5.0)
    assert error_codes == []

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)
    await client.close()
    await server.stop()


async def _open_sans_io_h2_connection(
    port: int,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """証明書検証を無効化した TLS 接続を開く (Sans-IO クライアント用)"""
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return await asyncio.open_connection("127.0.0.1", port, ssl=ssl_context)


def _send_all_h2_data(session: h2_low.Session, writer: asyncio.StreamWriter) -> None:
    """Sans-IO セッションの送信バッファを全てワイヤへ送出する"""
    while True:
        data = session.send()
        if data is None:
            break
        writer.write(data)


async def _pump_sans_io_h2(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    session: h2_low.Session,
    want_types: set[h2_low.EventType],
) -> list[h2_low.Event]:
    """Sans-IO h2.Session をサーバーと往復させ、目的種別のイベントを収集する

    送信バッファを全て送出し、受信データを処理する。want_types に該当する
    イベントが揃うか、接続終了 (EOF) ・タイムアウト (5 秒) まで繰り返す。
    発火したイベントは一覧で返し、テスト側で種別フィルターする。
    """
    events: list[h2_low.Event] = []
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        _send_all_h2_data(session, writer)
        await writer.drain()
        try:
            received = await asyncio.wait_for(reader.read(65535), timeout=0.2)
        except TimeoutError:
            continue
        if not received:
            break  # サーバーが接続を閉じた
        session.receive(received)
        while True:
            event = session.next_event()
            if event is None:
                break
            events.append(event)
        if any(e.type in want_types for e in events):
            break
    return events


async def _h2_server_with_sans_io_client(
    test_certificates: dict[str, str],
    on_session_request: Callable[
        [int, list[tuple[str, str]], tuple[object, ...]], Awaitable[int | None]
    ],
) -> tuple[Server, asyncio.StreamReader, asyncio.StreamWriter, h2_low.Session, int]:
    """高レベル Server を起動し、Sans-IO クライアントで CONNECT まで進める

    preface + SETTINGS 交換と、CONNECT を送信バッファへ積み終えた状態を
    返す (ワイヤへの送出は初回の pump で行われる)。呼び出し側で finally に
    writer / server の後始末を書くこと。

    @return (server, reader, writer, client, session_id)
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server.on_session_request(on_session_request)
    await server.start()

    reader, writer = await _open_sans_io_h2_connection(server.actual_port)
    client = h2_low.Session.create_client(h2_low.Config())

    # preface + SETTINGS を送出し、サーバーの SETTINGS を受信する
    _send_all_h2_data(client, writer)
    await writer.drain()
    received = await asyncio.wait_for(reader.read(65535), timeout=2.0)
    assert received
    client.receive(received)
    assert client.is_webtransport_ready() is True

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0

    return server, reader, writer, client, session_id


@pytest.mark.asyncio
async def test_h2_server_rejects_session_with_non_2xx(test_certificates):
    """on_session_request が 403 を返すとクライアントに SESSION_REJECTED が届くことを確認

    draft-15 Section 3.2 の Origin 検証失敗時の 403 SHOULD を高レベル
    Server から発行できることの検証。クライアントは高レベル Client では
    なく Sans-IO h2_low.Session を使い、非 2xx 受信時の SESSION_REJECTED
    イベントを直接観測する。
    """

    async def on_session_request(session_id, headers, addr):
        assert session_id >= 0
        assert any(name == ":path" and value == "/webtransport" for name, value in headers)
        # addr は peername の挙動 (IPv4 では 2-tuple) を担保する
        assert isinstance(addr, tuple) and len(addr) >= 2
        return 403

    server, reader, writer, client, session_id = await _h2_server_with_sans_io_client(
        test_certificates, on_session_request
    )
    try:
        events = await _pump_sans_io_h2(
            reader,
            writer,
            client,
            want_types={h2_low.EventType.SESSION_REJECTED},
        )
        rejected_events = [e for e in events if e.type == h2_low.EventType.SESSION_REJECTED]
        assert len(rejected_events) == 1
        assert rejected_events[0].session_id == session_id
        assert rejected_events[0].status_code == 403
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "decision", [None, 200, 299], ids=["none", "two_hundred", "two_ninety_nine"]
)
async def test_h2_server_accept_via_on_session_request(test_certificates, decision):
    """on_session_request が None または 200-299 を返すと accept 経路が動作することを確認

    コールバック未登録時と同等の後方互換挙動 (無条件 accept) を保ち、
    クライアント側は SESSION_READY を受信してセッションが確立する。
    200-299 を返した場合は accept 判定の経路を通る (299 は上限境界)。
    """
    server_ready_event = asyncio.Event()

    async def on_session_request(session_id, headers, addr):
        return decision

    async def on_session_ready(session_writer):
        server_ready_event.set()

    server, reader, writer, client, session_id = await _h2_server_with_sans_io_client(
        test_certificates, on_session_request
    )
    server.on_session_ready(on_session_ready)
    try:
        events = await _pump_sans_io_h2(
            reader,
            writer,
            client,
            want_types={h2_low.EventType.SESSION_READY},
        )
        ready_events = [e for e in events if e.type == h2_low.EventType.SESSION_READY]
        assert len(ready_events) == 1
        assert ready_events[0].session_id == session_id
        # サーバー側でも on_session_ready が発火して accept 経路が流れる
        await asyncio.wait_for(server_ready_event.wait(), timeout=2.0)
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


@pytest.mark.asyncio
async def test_h2_client_connect_returns_false_on_non_2xx_reject(test_certificates):
    """on_session_request が 403 を返すと Client.connect() が False を返すことを確認

    draft-15 Section 3.2 により、非 2xx 応答はセッション未確立を意味する。
    bindings は拒否時に SESSION_REJECTED のみを発火し、SESSION_READY /
    SESSION_CLOSED は発火しない。connect() の while ループが
    SESSION_REJECTED を検知しないと永久ブロックするため、実 Server と実
    Client を組み合わせて有限時間で False が返ることを検証する (修正前は
    wait_for のタイムアウトで失敗する)。
    """
    from webtransport.h2 import Client, Server

    async def on_session_request(session_id, headers, addr):
        return 403

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server.on_session_request(on_session_request)
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    try:
        connected = await asyncio.wait_for(client.connect(), timeout=5.0)
        assert connected is False
        assert client.is_connected is False
    finally:
        await client.close()
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code",
    [0, -1, 100, 600, False, 3.5],
    ids=["zero", "negative", "info", "too_high", "bool", "float"],
)
async def test_h2_server_on_session_request_invalid_status_raises_value_error(
    test_certificates,
    status_code,
):
    """on_session_request が範囲外値を返すと ValueError で接続が閉じることを確認

    HTTP status code として意味を持たない値 (0-199 / 600 以上 / bool /
    非 int) を silent に受け入れると :status が不正な値になるため、範囲
    チェックで ValueError を投げる。接続が閉じることでクライアント側は
    EOF を観測し、SESSION_READY / SESSION_REJECTED のどちらも受信しない
    (この検証はサーバープロセス内の例外を直接観測できないため間接的)。
    """

    async def on_session_request(session_id, headers, addr):
        return status_code

    server, reader, writer, client, _ = await _h2_server_with_sans_io_client(
        test_certificates, on_session_request
    )
    try:
        events = await _pump_sans_io_h2(
            reader,
            writer,
            client,
            want_types={
                h2_low.EventType.SESSION_READY,
                h2_low.EventType.SESSION_REJECTED,
                h2_low.EventType.SESSION_CLOSED,
            },
        )
        # 拒否・受理・終了のいずれも通知されない
        assert all(
            e.type
            not in (
                h2_low.EventType.SESSION_READY,
                h2_low.EventType.SESSION_REJECTED,
                h2_low.EventType.SESSION_CLOSED,
            )
            for e in events
        )
        # 接続が閉じられたことを確認する (EOF)
        remaining = await asyncio.wait_for(reader.read(65535), timeout=1.0)
        assert remaining == b""
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
