"""webtransport.h2 (WebTransport over HTTP/2) 高レベル API テスト"""

import asyncio
import socket
import ssl
import time
from collections.abc import Awaitable, Callable

import pytest

from webtransport import http2
from webtransport.exceptions import (
    ConnectRefusedError,
    ConnectTimeoutError,
    HandshakeFailedError,
)
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

    await client.connect()

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

    await client.connect()

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
    await client.connect()

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
    """クライアントの close_session がサーバーに通知されることを確認

    close() は run() の受信ループが動いていないとピアの CONNECT ストリーム
    クローズを観測できない。固定 sleep ではなく「サーバーが送ったデータグラム
    をクライアントの run() が受信した」という観測を待つことで、run() の起動を
    決定的に確認してから close() する。
    """
    from webtransport.h2 import Client, Server

    session_closed = asyncio.Event()
    client_ready = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_writer) -> None:
        # run() が動いていなければクライアントはこのデータグラムを処理できない
        await session_writer.send_datagram(b"ready")

    async def on_session_closed(session_writer) -> None:
        session_closed.set()

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)
    await server.start()

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )

    async def on_datagram(data: bytes) -> None:
        client_ready.set()

    client.on_datagram(on_datagram)
    await client.connect()

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    await asyncio.wait_for(client_ready.wait(), timeout=5.0)
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
    await client.connect()

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
    await client.connect()

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
    await client.connect()
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
    await client.connect()
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
    await client.connect()
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
    await client.connect()

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
    await client.connect()
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
async def test_h2_client_connect_raises_on_non_2xx_reject(test_certificates):
    """on_session_request が 403 を返すと Client.connect() が HandshakeFailedError を送出することを確認

    draft-15 Section 3.2 により、非 2xx 応答はセッション未確立を意味する。
    bindings は拒否時に SESSION_REJECTED のみを発火し、SESSION_READY /
    SESSION_CLOSED は発火しない。connect() の待機ループが
    SESSION_REJECTED を検知しないと永久ブロックするため、実 Server と実
    Client を組み合わせて有限時間で HandshakeFailedError が送出されることを
    検証する (修正前は wait_for のタイムアウトで失敗する)。
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
        with pytest.raises(HandshakeFailedError):
            await asyncio.wait_for(client.connect(), timeout=5.0)
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


@pytest.mark.asyncio
async def test_client_on_session_ready_fires(test_certificates):
    """クライアントの on_session_ready コールバックが発火することを確認

    connect() は SESSION_READY イベントを消費して確立判定を行うが、
    コールバック登録の順序に依存せず run() のイベントループで
    on_session_ready が 1 回発火することを検証する (修正前は connect() が
    イベントを消費してしまうため、コールバックが一度も呼ばれなかった)。
    """
    from webtransport.h2 import Client, Server

    ready_event = asyncio.Event()
    ready_stream_ids: list[int] = []

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

    async def on_client_session_ready(session_id: int) -> None:
        ready_stream_ids.append(session_id)
        ready_event.set()

    # connect() の前にコールバックを登録する (connect() 中に発火する
    # SESSION_READY が消費されても、run() で発火することを確認する)
    client.on_session_ready(on_client_session_ready)

    await client.connect()

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(ready_event.wait(), timeout=5.0)

    # 追加発火がないことを確認するための settle 待ち (イベント待ちでは
    # 表現できない)
    await asyncio.sleep(0.1)
    assert ready_stream_ids == [client.session_id]

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_client_on_session_ready_after_connect(test_certificates):
    """connect() の後にコールバックを登録しても on_session_ready が発火することを確認

    __aenter__ が connect() を先に実行する利用形態 (async with で既に
    connect() が完了している場合も含む) で、connect() 後に on_session_ready
    を登録しても発火が保証されることを検証する。無登録のまま connect() が
    終わっても、イベントは未配信バッファに保持され、登録後の run() で
    発火する。
    """
    from webtransport.h2 import Client, Server

    ready_event = asyncio.Event()
    ready_stream_ids: list[int] = []

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

    await client.connect()

    # connect() の後にコールバックを登録する
    async def on_client_session_ready(session_id: int) -> None:
        ready_stream_ids.append(session_id)
        ready_event.set()

    client.on_session_ready(on_client_session_ready)

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    await asyncio.wait_for(ready_event.wait(), timeout=5.0)

    # 追加発火がないことを確認するための settle 待ち (イベント待ちでは
    # 表現できない)
    await asyncio.sleep(0.1)
    assert ready_stream_ids == [client.session_id]

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)

    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_connect_timeout_on_listen_only_server():
    """accept しない TCP サーバーに対して connect() が ConnectTimeoutError を送出することを確認する

    前提: listen のみで accept しない TCP サーバーには TCP ハンドシェイクが
    通るが TLS 応答が返らない。
    期待値: timeout=1.0 で 1 秒強で ConnectTimeoutError が送出される。
    """
    from webtransport.h2 import Client

    # accept しないリスナーを用意する (カーネルの backlog が TCP
    # ハンドシェイクを完了させるため、TLS 層で停滞する)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    client = Client(
        url=f"https://127.0.0.1:{port}/webtransport",
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
        # 確立済み TCP 接続とリスナーを後始末する
        await client.close()
        listener.close()


@pytest.mark.asyncio
async def test_connect_refused_on_closed_port():
    """閉じたポートに対して connect() が ConnectRefusedError を送出することを確認する

    前提: 127.0.0.1:1 は閉じており TCP RST が返る。
    期待値: Python 標準の ConnectionRefusedError を原因として保持する
    ConnectRefusedError が即座に送出される。
    """
    from webtransport.h2 import Client

    client = Client(
        url="https://127.0.0.1:1/webtransport",
        verify_peer=False,
    )
    try:
        with pytest.raises(ConnectRefusedError) as exc_info:
            await client.connect(timeout=5.0)
        # 自前の ConnectRefusedError ではなく builtin の ConnectionRefusedError
        # (綴りが 3 文字違い) が __cause__ に保持される
        assert isinstance(exc_info.value.__cause__, ConnectionRefusedError)
        assert client.is_connected is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_stop_while_client_connected(test_certificates):
    """クライアント接続中に stop() が復帰する"""
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
    await client.connect()

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass
        except OSError:
            # サーバー停止による TCP 切断も終了として扱う
            pass

    client_task = asyncio.create_task(run_client())
    # タスクが起動して接続中であることを確認する (停止後の終了と区別する)
    await asyncio.sleep(0.05)
    assert not client_task.done()
    try:
        # 接続中の stop() は 500 ms 以内に復帰する
        start = time.monotonic()
        await server.stop()
        assert time.monotonic() - start < 0.5

        # クライアントは TCP 切断を検知して run() が終了する
        await asyncio.wait_for(client_task, timeout=5.0)
    finally:
        if not client_task.done():
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
        try:
            await client.close()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_goaway_notifies_server_and_keeps_session(test_certificates):
    """GOAWAY 受信でサーバーに通知し既存セッションが継続することを確認

    高レベル Server と Sans-IO クライアントで検証する。GOAWAY を 2 回
    送っても on_goaway は 1 回のみ発火し、データグラムの送受信は継続する
    (draft-15 Section 6.13 の graceful shutdown)。
    """
    from conftest import _encode_goaway_frame

    async def allow_all(session_id, headers, addr):
        return None

    goaway_calls: list[tuple[int, int, tuple]] = []
    goaway_event = asyncio.Event()
    datagram_received: list[bytes] = []
    datagram_event = asyncio.Event()

    async def on_goaway(last_stream_id, error_code, addr):
        goaway_calls.append((last_stream_id, error_code, addr))
        goaway_event.set()

    async def on_datagram(data: bytes, session_writer) -> None:
        datagram_received.append(data)
        datagram_event.set()

    server, reader, writer, client, session_id = await _h2_server_with_sans_io_client(
        test_certificates, allow_all
    )
    server.on_goaway(on_goaway)
    server.on_datagram(on_datagram)
    try:
        # セッションを確立する
        events = await _pump_sans_io_h2(
            reader, writer, client, want_types={h2_low.EventType.SESSION_READY}
        )
        assert [e for e in events if e.type == h2_low.EventType.SESSION_READY]

        # GOAWAY を 2 回送る。last_stream_id はサーバー起点ストリームを
        # 指すため 0 とする (parity 不整合の値は nghttp2 が黙って無視するため)
        writer.write(_encode_goaway_frame(0, 0))
        await writer.drain()
        writer.write(_encode_goaway_frame(0, 0))
        await writer.drain()
        await asyncio.wait_for(goaway_event.wait(), timeout=5.0)
        await asyncio.sleep(0.3)

        # 初回のみ 1 回発火する
        assert len(goaway_calls) == 1
        assert goaway_calls[0][0] == 0
        assert goaway_calls[0][1] == 0
        assert isinstance(goaway_calls[0][2], tuple)

        # 既存セッションでデータグラムの受信が継続する
        client.send_datagram(session_id, b"after-goaway")
        _send_all_h2_data(client, writer)
        await writer.drain()
        await asyncio.wait_for(datagram_event.wait(), timeout=5.0)
        assert datagram_received == [b"after-goaway"]
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()


async def _serve_fake_h2_goaway(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """最小応答の手続きサーバーで 1 接続を処理する

    SETTINGS 交換と 2xx 応答の後、届いた DATA フレーム (ストリーム 1)
    をそのまま返し、初回転送後に GOAWAY を 2 回送る (重複抑止の検証用)。
    高レベル Client の GOAWAY 関連テストで共用する。
    """
    from conftest import _encode_goaway_frame

    # クライアントの preface + SETTINGS を読む
    try:
        await asyncio.wait_for(reader.read(65535), timeout=5.0)
    except TimeoutError:
        writer.close()
        return
    # SETTINGS (空ではなく WebTransport 有効化) を返す
    settings = (
        (0x0008).to_bytes(2, "big")
        + (1).to_bytes(4, "big")
        + (0x2B60).to_bytes(2, "big")
        + (1).to_bytes(4, "big")
    )
    writer.write(
        len(settings).to_bytes(3, "big") + bytes([0x04, 0x00]) + (0).to_bytes(4, "big") + settings
    )
    await writer.drain()
    # CONNECT を読む
    try:
        await asyncio.wait_for(reader.read(65535), timeout=5.0)
    except TimeoutError:
        writer.close()
        return
    # 2xx (:status 200) を返す
    writer.write(
        (1).to_bytes(3, "big") + bytes([0x01, 0x04]) + (1).to_bytes(4, "big") + bytes([0x88])
    )
    await writer.drain()
    sent_goaway = False
    buffer = b""
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(65535), timeout=5.0)
            except TimeoutError:
                continue
            if not chunk:
                break
            buffer += chunk
            # 届いた DATA フレーム (ストリーム 1) をそのまま返す
            while len(buffer) >= 9:
                length = int.from_bytes(buffer[0:3], "big")
                frame_type = buffer[3]
                stream_id = int.from_bytes(buffer[5:9], "big") & 0x7FFFFFFF
                if len(buffer) < 9 + length:
                    break
                frame = buffer[: 9 + length]
                buffer = buffer[9 + length :]
                if frame_type == 0x00 and stream_id == 1 and length > 0:
                    writer.write(frame)
                    await writer.drain()
                    # 初回 DATA 転送後に GOAWAY を 2 回送る (重複抑止の検証用)
                    if not sent_goaway:
                        sent_goaway = True
                        writer.write(_encode_goaway_frame(1, 0))
                        await writer.drain()
                        writer.write(_encode_goaway_frame(1, 0))
                        await writer.drain()
    except ConnectionError, OSError:
        pass
    finally:
        writer.close()


@pytest.mark.asyncio
async def test_goaway_notifies_client_and_keeps_session(test_certificates):
    """GOAWAY 受信でクライアントに通知し既存セッションが継続することを確認

    高レベル Client と最小応答の手続きサーバーで検証する。手続き
    サーバーは SETTINGS 交換と 2xx 応答と DATA 転送と GOAWAY 送出のみを
    行う。GOAWAY 前後でデータグラムの往復が継続し、on_goaway は 1 回
    のみ発火する。
    """
    from webtransport.h2 import Client

    goaway_calls: list[tuple[int, int]] = []
    echoes: list[bytes] = []
    echo_event = asyncio.Event()

    async def handle_fake_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await _serve_fake_h2_goaway(reader, writer)

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(test_certificates["certfile"], test_certificates["keyfile"])
    ssl_context.set_alpn_protocols(["h2"])
    fake_server = await asyncio.start_server(handle_fake_server, "127.0.0.1", 0, ssl=ssl_context)
    port = fake_server.sockets[0].getsockname()[1]
    try:
        client = Client(url=f"https://127.0.0.1:{port}/webtransport", verify_peer=False)

        async def on_goaway(last_stream_id, error_code):
            goaway_calls.append((last_stream_id, error_code))

        async def on_datagram(data: bytes) -> None:
            echoes.append(data)
            echo_event.set()

        client.on_goaway(on_goaway)
        client.on_datagram(on_datagram)
        await client.connect()

        async def run_client():
            try:
                await client.run()
            except asyncio.CancelledError:
                pass

        client_task = asyncio.create_task(run_client())
        try:
            # GOAWAY 前の往復
            await client.send_datagram(b"before-goaway")
            await asyncio.wait_for(echo_event.wait(), timeout=5.0)
            assert echoes == [b"before-goaway"]
            echo_event.clear()

            # GOAWAY 観測を待つ (2 回送付でも 1 回のみ発火する)
            for _ in range(100):
                if goaway_calls:
                    break
                await asyncio.sleep(0.05)
            assert len(goaway_calls) == 1
            assert goaway_calls[0] == (1, 0)
            await asyncio.sleep(0.3)
            assert len(goaway_calls) == 1

            # GOAWAY 後の往復が継続する
            await client.send_datagram(b"after-goaway")
            await asyncio.wait_for(echo_event.wait(), timeout=5.0)
            assert echoes == [b"before-goaway", b"after-goaway"]
            assert client._goaway_notified is True
        finally:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
            await client.close()
    finally:
        fake_server.close()
        await fake_server.wait_closed()


@pytest.mark.asyncio
async def test_goaway_notified_again_after_reconnect(test_certificates):
    """再接続後は GOAWAY 通知が再び発火することを確認

    同一 Client インスタンスの使い回しで通知済み印が残らない。
    """
    from webtransport.h2 import Client

    goaway_calls: list[tuple[int, int]] = []

    async def handle_fake_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        await _serve_fake_h2_goaway(reader, writer)

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(test_certificates["certfile"], test_certificates["keyfile"])
    ssl_context.set_alpn_protocols(["h2"])
    fake_server = await asyncio.start_server(handle_fake_server, "127.0.0.1", 0, ssl=ssl_context)
    port = fake_server.sockets[0].getsockname()[1]
    try:
        client = Client(url=f"https://127.0.0.1:{port}/webtransport", verify_peer=False)

        async def on_goaway(last_stream_id, error_code):
            goaway_calls.append((last_stream_id, error_code))

        client.on_goaway(on_goaway)

        async def run_client():
            try:
                await client.run()
            except asyncio.CancelledError:
                pass

        # 1 本目の接続で GOAWAY を観測する
        await client.connect()
        client_task = asyncio.create_task(run_client())
        try:
            await client.send_datagram(b"first")
            for _ in range(100):
                if goaway_calls:
                    break
                await asyncio.sleep(0.05)
            assert len(goaway_calls) == 1
        finally:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
            await client.close()

        # 再接続後も GOAWAY が通知される
        await client.connect()
        client_task = asyncio.create_task(run_client())
        try:
            await client.send_datagram(b"second")
            for _ in range(100):
                if len(goaway_calls) >= 2:
                    break
                await asyncio.sleep(0.05)
            assert len(goaway_calls) == 2
        finally:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
            await client.close()
    finally:
        fake_server.close()
        await fake_server.wait_closed()


@pytest.mark.asyncio
async def test_server_start_with_config_over_limit_raises_value_error(test_certificates):
    """Config の上限値超えで Server.start が ValueError になることを確認

    Server.start は使い捨てのセッション生成で Config を検証するため、
    接続を待ち受ける前に設定ミスを検出する (fail-fast)。
    """
    config = h2_low.Config()
    config.wt_initial_max_data = 2**32
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        config=config,
    )

    try:
        with pytest.raises(ValueError, match=r"wt_initial_max_data must be less than 2\^32"):
            await server.start()
        # バインド前に拒否され、リスナーが残らない
        assert server.is_running is False
        assert server._server is None
        assert server.actual_port == 0
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_client_connect_with_config_over_limit_raises_value_error(test_certificates):
    """Config の上限値超えで Client.connect が ValueError になり接続が残らないことを確認

    TLS 接続後にセッション生成が ValueError になった場合も writer を閉じて
    から送出し、接続を開いたままにしない。
    """
    from webtransport.h2 import Client

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()
    config = h2_low.Config()
    config.wt_initial_max_streams_bidi = 2**32
    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
        config=config,
    )
    try:
        with pytest.raises(
            ValueError, match=r"wt_initial_max_streams_bidi must be less than 2\^32"
        ):
            await client.connect(timeout=5.0)
        assert client.is_connected is False
        # 後始末で接続が閉じられている (writer / reader が残らない)
        assert client._writer is None
        assert client._reader is None
    finally:
        await server.stop()


async def _serve_silent_h2(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """SETTINGS 交換と 2xx 応答のみ行い、以後は応答も切断もせず沈黙する手続きサーバー

    close() の待機タイムアウト検証で使う。クライアントの WT_CLOSE_SESSION
    を含む DATA フレームを受け取っても応答せず、接続も維持する。
    """
    # クライアントの preface + SETTINGS を読む
    try:
        await asyncio.wait_for(reader.read(65535), timeout=5.0)
    except TimeoutError:
        writer.close()
        return
    # SETTINGS (WebTransport 有効化) を返す
    settings = (
        (0x0008).to_bytes(2, "big")
        + (1).to_bytes(4, "big")
        + (0x2B60).to_bytes(2, "big")
        + (1).to_bytes(4, "big")
    )
    writer.write(
        len(settings).to_bytes(3, "big") + bytes([0x04, 0x00]) + (0).to_bytes(4, "big") + settings
    )
    await writer.drain()
    # CONNECT を読む
    try:
        await asyncio.wait_for(reader.read(65535), timeout=5.0)
    except TimeoutError:
        writer.close()
        return
    # 2xx (:status 200) を返す
    writer.write(
        (1).to_bytes(3, "big") + bytes([0x01, 0x04]) + (1).to_bytes(4, "big") + bytes([0x88])
    )
    await writer.drain()
    # 以後は届いたデータを読み捨てて応答しない (接続も閉じない)
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(65535), timeout=5.0)
            except TimeoutError:
                continue
            if not chunk:
                break
    except ConnectionError, OSError:
        pass
    finally:
        writer.close()


@pytest.mark.asyncio
async def test_client_close_waits_for_peer_fin(test_certificates):
    """close() がピアの CONNECT ストリームクローズを待ってから閉じることを確認

    通常経路ではサーバーが WT_CLOSE_SESSION 応答の END_STREAM を返すため、
    待機結果が peer-closed になる。既定の待機上限が 3 秒であることも確認する。
    """
    from webtransport.h2 import Client, Server

    assert Client(url="https://127.0.0.1:4433/webtransport")._close_wait_timeout == 3.0

    session_closed_event = asyncio.Event()

    async def on_session_closed(session_writer):
        session_closed_event.set()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server.on_session_closed(on_session_closed)
    await server.start()
    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    try:
        await client.connect()
        await client.close()
        assert client._close_wait_result == "peer-closed"
        # WT_CLOSE_SESSION がサーバーに届き、セッション終了が通知される
        await asyncio.wait_for(session_closed_event.wait(), timeout=5.0)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_client_close_waits_while_run_active(test_certificates):
    """run() 実行中に close() してもピアクローズを観測して閉じることを確認

    run() の記録を close() が参照する経路と、run() 終了後に close() 自身が
    受信して観測する経路のどちらでも、待機結果が peer-closed になる。
    """
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
    await client.connect()

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    try:
        # run() が実行中になるまで待ってから close() する (_run_active 待機の検証)
        for _ in range(100):
            if client._run_active:
                break
            await asyncio.sleep(0.01)
        assert client._run_active is True
        await client.close()
        assert client._close_wait_result == "peer-closed"
    finally:
        client_task.cancel()
        await asyncio.gather(client_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_client_close_from_run_callback(test_certificates):
    """run() のコールバックから close() を呼んでもタイムアウトせずに閉じることを確認

    run() がコールバックを await している間は _run_active が True のままだが、
    同一タスクからの close() は自身で受信してピアクローズを観測する。
    """
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
        close_wait_timeout=1.0,
    )
    closed = asyncio.Event()

    async def on_session_ready(session_id):
        await client.close()
        closed.set()

    client.on_session_ready(on_session_ready)
    await client.connect()

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    try:
        await asyncio.wait_for(closed.wait(), timeout=5.0)
        assert client._close_wait_result == "peer-closed"
    finally:
        client_task.cancel()
        await asyncio.gather(client_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_client_close_from_run_callback_child_task(test_certificates):
    """run() のコールバックが子タスクで close() を呼んでも観測できることを確認

    コールバックが子タスク化して close() を待つ場合も、run() は受信して
    いないため close() が自身で受信してピアクローズを観測する。
    """
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
        close_wait_timeout=1.0,
    )
    closed = asyncio.Event()

    async def on_session_ready(session_id):
        await asyncio.gather(client.close())
        closed.set()

    client.on_session_ready(on_session_ready)
    await client.connect()

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())
    try:
        await asyncio.wait_for(closed.wait(), timeout=5.0)
        assert client._close_wait_result == "peer-closed"
    finally:
        client_task.cancel()
        await asyncio.gather(client_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_client_close_idempotent_and_unconnected(test_certificates):
    """二重 close() と未接続 close() が安全で待機結果を保つことを確認"""
    from webtransport.h2 import Client, Server

    # 未接続の close() は待機せずに結果 none のまま完了する
    unconnected = Client(url="https://127.0.0.1:4433/webtransport", verify_peer=False)
    await unconnected.close()
    assert unconnected._close_wait_result == "none"

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
    try:
        await client.connect()
        await client.close()
        assert client._close_wait_result == "peer-closed"
        # 二重 close() は待機を繰り返さず結果を保つ
        await client.close()
        assert client._close_wait_result == "peer-closed"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_client_close_times_out_without_peer_fin(test_certificates):
    """ピアが応答しなくても close() が上限で完了することを確認

    2xx 応答後に沈黙する手続きサーバーに対し、短い上限で待機が打ち切られ、
    待機結果が timeout になる。実時間で上限いっぱい待つことを確認する。
    """
    from webtransport.h2 import Client

    async def handle_silent_server(reader, writer):
        await _serve_silent_h2(reader, writer)

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(test_certificates["certfile"], test_certificates["keyfile"])
    ssl_context.set_alpn_protocols(["h2"])
    fake_server = await asyncio.start_server(handle_silent_server, "127.0.0.1", 0, ssl=ssl_context)
    port = fake_server.sockets[0].getsockname()[1]
    client = Client(
        url=f"https://127.0.0.1:{port}/webtransport",
        verify_peer=False,
        close_wait_timeout=0.5,
    )
    try:
        await client.connect()
        # 上限いっぱい待って打ち切られる。上限超過の検出力のため下限を
        # 主眼とし、上限側は CI 変動の余裕を持たせる
        start = time.monotonic()
        await client.close()
        elapsed = time.monotonic() - start
        assert client._close_wait_result == "timeout"
        assert 0.4 <= elapsed < 2.0
    finally:
        fake_server.close()
        await fake_server.wait_closed()


@pytest.mark.asyncio
async def test_client_close_skipped_without_wait(test_certificates):
    """close_wait_timeout=0 では待たずに閉じ、待機結果が skipped になることを確認

    WT_CLOSE_SESSION は送出されるためサーバー側にもセッション終了が届く。
    """
    from webtransport.h2 import Client, Server

    session_closed_event = asyncio.Event()

    async def on_session_closed(session_writer):
        session_closed_event.set()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server.on_session_closed(on_session_closed)
    await server.start()
    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
        close_wait_timeout=0,
    )
    try:
        await client.connect()
        await client.close()
        assert client._close_wait_result == "skipped"
        await asyncio.wait_for(session_closed_event.wait(), timeout=5.0)
    finally:
        await server.stop()


async def _h2_server_with_http2_connection_client(
    test_certificates: dict[str, str],
    on_session_request: Callable[
        [int, list[tuple[str, str]], tuple[object, ...]], Awaitable[int | None]
    ],
) -> tuple[Server, asyncio.StreamReader, asyncio.StreamWriter, http2.Connection]:
    """高レベル Server を起動し、http2.Connection (Sans-IO) で SETTINGS まで進める

    h2_low.Session は SESSION_REJECTED の headers が空で拒否応答のヘッダーを
    観測できないため、応答ヘッダーを見るテストでは http2.Connection を使う。

    @return (server, reader, writer, client)
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
    client = http2.Connection.create_client(http2.Config())

    # preface + SETTINGS を送出し、サーバーの SETTINGS を受信する
    _send_all_http2_data(client, writer)
    await writer.drain()
    received = await asyncio.wait_for(reader.read(65535), timeout=2.0)
    assert received
    client.receive(received)

    return server, reader, writer, client


def _send_all_http2_data(client: http2.Connection, writer: asyncio.StreamWriter) -> None:
    """Sans-IO http2.Connection の送信バッファを全てワイヤへ送出する"""
    while True:
        data = client.send()
        if data is None:
            break
        writer.write(data)


async def _pump_http2_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    client: http2.Connection,
    want_types: set[http2.EventType],
) -> list[http2.Event]:
    """Sans-IO http2.Connection をサーバーと往復させ、目的種別のイベントを収集する

    want_types に該当するイベントが揃うか、接続終了 (EOF)・タイムアウト
    (5 秒) まで繰り返す。
    """
    events: list[http2.Event] = []
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        _send_all_http2_data(client, writer)
        await writer.drain()
        try:
            received = await asyncio.wait_for(reader.read(65535), timeout=0.2)
        except TimeoutError:
            continue
        if not received:
            break  # サーバーが接続を閉じた
        client.receive(received)
        while True:
            event = client.next_event()
            if event is None:
                break
            events.append(event)
        if any(e.type in want_types for e in events):
            break
    return events


@pytest.mark.asyncio
async def test_h2_server_rejects_session_with_405_and_allow(test_certificates):
    """on_session_request の 405 拒否で :status 405 と allow: CONNECT が届くことを確認

    draft-ietf-webtrans-http2-15 Section 3.2 の 405 SHOULD を高レベル
    Server のアプリコールバック (on_session_request) から発行できることを、
    TLS / asyncio を挟んだ実経路で検証する。RFC 9110 Section 15.5.6 の MUST
    に従い Allow: CONNECT が応答ヘッダーに載る。応答ヘッダーの観測には
    http2.Connection (Sans-IO) を使う (h2.Session は headers が空)。
    """

    async def on_session_request(session_id, headers, addr):
        return 405

    server, reader, writer, client = await _h2_server_with_http2_connection_client(
        test_certificates, on_session_request
    )
    try:
        stream_id = client.submit_request(
            [
                (":method", "CONNECT"),
                (":protocol", "webtransport"),
                (":scheme", "https"),
                (":authority", "localhost"),
                (":path", "/webtransport"),
            ]
        )
        assert stream_id > 0
        client.send_data(stream_id, b"", eof=True)

        events = await _pump_http2_connection(
            reader,
            writer,
            client,
            want_types={http2.EventType.HEADERS},
        )
        headers_events = [e for e in events if e.type == http2.EventType.HEADERS]
        assert len(headers_events) == 1
        assert headers_events[0].stream_id == stream_id
        response_headers = dict(headers_events[0].headers)
        assert response_headers[":status"] == "405"
        assert response_headers["allow"] == "CONNECT"
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
