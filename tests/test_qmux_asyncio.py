"""QMux の asyncio クライアントとサーバーの E2E テスト

実ソケット (TCP / TLS) を使い、モックは使わない。QMux はトランスポート
パラメータを帯域内で交換するため、接続が確立すると `on_session_ready` (サーバー)
と `on_connected` (クライアント) が発火する。
"""

from __future__ import annotations

import asyncio
import contextlib
import ssl

import pytest

from webtransport import qmux

# テストの待ち時間の上限 (秒)
TIMEOUT = 10.0


async def _stop_server(server: qmux.Server, task: asyncio.Task[None]) -> None:
    """サーバーを停止し、実行タスクを片付ける"""
    await server.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_stream_data_is_echoed_over_tcp() -> None:
    """TCP 上の QMux で双方向ストリームのデータが往復する"""
    server = qmux.Server("127.0.0.1", 0)
    server_received: list[tuple[int, bytes, bool]] = []

    async def on_server_stream_data(
        session: qmux.Session,
        stream_id: int,
        data: bytes,
        fin: bool,
    ) -> None:
        server_received.append((stream_id, data, fin))
        # 双方向ストリーム (stream_id % 4 == 0) のみエコーする
        if stream_id % 4 == 0:
            await session.send_stream_data(stream_id, data, fin=fin)

    server.on_stream_data(on_server_stream_data)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port)
    echoed: list[bytes] = []
    echo_received = asyncio.Event()

    async def on_client_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        echoed.append(data)
        echo_received.set()

    client.on_stream_data(on_client_stream_data)
    client_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        client_task = asyncio.create_task(client.run())
        stream_id = await client.open_stream()
        assert stream_id >= 0
        await client.send_stream_data(stream_id, b"hello qmux", fin=True)
        await asyncio.wait_for(echo_received.wait(), TIMEOUT)
        assert echoed == [b"hello qmux"]
        assert server_received == [(stream_id, b"hello qmux", True)]
    finally:
        await client.close()
        if client_task is not None:
            await asyncio.wait_for(client_task, TIMEOUT)
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_server_opens_unidirectional_stream() -> None:
    """サーバーから単方向ストリームを開いて送信できる"""
    server = qmux.Server("127.0.0.1", 0)
    server_stream_id = -1

    async def on_session_ready(session: qmux.Session) -> None:
        nonlocal server_stream_id
        server_stream_id = await session.open_stream(unidirectional=True)
        await session.send_stream_data(server_stream_id, b"from server", fin=True)

    server.on_session_ready(on_session_ready)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port)
    received: list[tuple[int, bytes, bool]] = []
    received_event = asyncio.Event()

    async def on_client_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        received.append((stream_id, data, fin))
        received_event.set()

    client.on_stream_data(on_client_stream_data)
    client_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        client_task = asyncio.create_task(client.run())
        await asyncio.wait_for(received_event.wait(), TIMEOUT)
        assert received == [(server_stream_id, b"from server", True)]
        # サーバーが開いたのは単方向ストリーム (stream_id % 4 == 2 か 3)
        assert server_stream_id % 4 in (2, 3)
    finally:
        await client.close()
        if client_task is not None:
            await asyncio.wait_for(client_task, TIMEOUT)
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_client_close_notifies_server() -> None:
    """クライアントが閉じるとサーバーの on_session_closed が発火する"""
    server = qmux.Server("127.0.0.1", 0)
    closed_sessions: list[int] = []
    closed_event = asyncio.Event()

    async def on_session_closed(session: qmux.Session) -> None:
        closed_sessions.append(session.session_id)
        closed_event.set()

    server.on_session_closed(on_session_closed)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port, close_reason="bye")
    try:
        await client.connect()
        assert client.is_connected is True
        client_task = asyncio.create_task(client.run())
        await client.close()
        await asyncio.wait_for(client_task, TIMEOUT)
        await asyncio.wait_for(closed_event.wait(), TIMEOUT)
        assert closed_sessions == [1]
    finally:
        await client.close()
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_handshake_completes_on_both_sides() -> None:
    """接続直後に両側でハンドシェイクが完了する"""
    server = qmux.Server("127.0.0.1", 0)
    server_ready = asyncio.Event()

    async def on_session_ready(session: qmux.Session) -> None:
        server_ready.set()

    server.on_session_ready(on_session_ready)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port)
    connected = asyncio.Event()

    async def on_connected() -> None:
        connected.set()

    client.on_connected(on_connected)
    try:
        # connect() はトランスポートパラメータの交換が終わるまで待つ
        await client.connect()
        assert connected.is_set()
        await asyncio.wait_for(server_ready.wait(), TIMEOUT)
        assert client.peer_address is not None
        assert client.peer_address[0] == "127.0.0.1"
        assert client.negotiated_alpn_protocol is None
    finally:
        await client.close()
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_tls_with_alpn(test_certificates: dict[str, str]) -> None:
    """TLS 上で ALPN を交渉して QMux を動かせる"""
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(
        test_certificates["certfile"],
        test_certificates["keyfile"],
    )
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # 自己署名証明書を使うため検証しない (ALPN の交渉だけを確認する)
    client_context.check_hostname = False
    client_context.verify_mode = ssl.CERT_NONE

    server = qmux.Server(
        "127.0.0.1",
        0,
        ssl=server_context,
        alpn_protocols=["qmux-test"],
    )
    received: list[bytes] = []
    received_event = asyncio.Event()

    async def on_server_stream_data(
        session: qmux.Session,
        stream_id: int,
        data: bytes,
        fin: bool,
    ) -> None:
        received.append(data)
        received_event.set()

    server.on_stream_data(on_server_stream_data)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client(
        "127.0.0.1",
        server.actual_port,
        ssl=client_context,
        alpn_protocols=["qmux-test"],
    )
    client_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        assert client.negotiated_alpn_protocol == "qmux-test"
        client_task = asyncio.create_task(client.run())
        stream_id = await client.open_stream()
        await client.send_stream_data(stream_id, b"over tls", fin=True)
        await asyncio.wait_for(received_event.wait(), TIMEOUT)
        assert received == [b"over tls"]
    finally:
        await client.close()
        if client_task is not None:
            await asyncio.wait_for(client_task, TIMEOUT)
        await _stop_server(server, server_task)


def test_alpn_protocols_is_required_with_ssl() -> None:
    """ssl を渡したのに alpn_protocols が無い場合は ValueError になる"""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with pytest.raises(ValueError, match="alpn_protocols"):
        qmux.Client("127.0.0.1", 4433, ssl=context)
    with pytest.raises(ValueError, match="alpn_protocols"):
        qmux.Server("127.0.0.1", 4433, ssl=context)


def test_alpn_protocols_requires_ssl() -> None:
    """alpn_protocols だけを渡した場合は ValueError になる"""
    with pytest.raises(ValueError, match="requires ssl"):
        qmux.Client("127.0.0.1", 4433, alpn_protocols=["qmux"])
    with pytest.raises(ValueError, match="requires ssl"):
        qmux.Server("127.0.0.1", 4433, alpn_protocols=["qmux"])


@pytest.mark.asyncio
async def test_run_before_connect_raises() -> None:
    """connect() の前に run() や send_stream_data() を呼ぶと RuntimeError になる"""
    client = qmux.Client("127.0.0.1", 4433)
    with pytest.raises(RuntimeError, match="connect"):
        await client.run()
    with pytest.raises(RuntimeError, match="connect"):
        await client.send_stream_data(0, b"data")


@pytest.mark.asyncio
async def test_server_accepts_multiple_connections() -> None:
    """複数の接続を同時に受け付けられる"""
    server = qmux.Server("127.0.0.1", 0)
    session_ids: list[int] = []
    sessions_event = asyncio.Event()

    async def on_session_ready(session: qmux.Session) -> None:
        session_ids.append(session.session_id)
        if len(session_ids) >= 2:
            sessions_event.set()

    server.on_session_ready(on_session_ready)

    await server.start()
    server_task = asyncio.create_task(server.run())

    clients = [
        qmux.Client("127.0.0.1", server.actual_port),
        qmux.Client("127.0.0.1", server.actual_port),
    ]
    try:
        for client in clients:
            await client.connect()
        await asyncio.wait_for(sessions_event.wait(), TIMEOUT)
        assert sorted(session_ids) == [1, 2]
    finally:
        for client in clients:
            await client.close()
        await _stop_server(server, server_task)
