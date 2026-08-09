"""QUIC クライアントの connect タイムアウトと max_datagram_frame_size のテスト

高レベル Client の connect(timeout=...) によるハンドシェイク打ち切りと、
max_datagram_frame_size による DATAGRAM 広告の制御を検証する。DATAGRAM の
広告有無はサーバー側の低レベル接続の remote_max_datagram_frame_size で
観測する。
"""

from __future__ import annotations

import asyncio

import pytest

from webtransport.quic import Client, Server


async def _run_server(server: Server) -> None:
    """サーバーのメインループを実行する (キャンセルで終了)

    Args:
        server: 実行するサーバー
    """
    try:
        await server.run()
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_connect_timeout(test_certificates):
    """ハンドシェイク未完了のまま期限に達したら connect が False を返すことを確認する

    サーバーがハンドシェイクを進めない (応答しない) ため、connect(timeout=短い値)
    が期限で打ち切られ False を返すことを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    # サーバーの run を起動しない (ハンドシェイクを進めない)
    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    connected = await asyncio.wait_for(client.connect(timeout=0.5), timeout=5.0)
    assert connected is False, "期限までにハンドシェイクが完了しない場合は False になるべき"

    # タイムアウト後も接続は存続し得る (ハンドシェイクが後で完了する可能性がある)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_connect_timeout_non_positive(test_certificates):
    """timeout <= 0 のときは接続を開始せず即座に False を返すことを確認する"""
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    assert await asyncio.wait_for(client.connect(timeout=0), timeout=5.0) is False
    assert await asyncio.wait_for(client.connect(timeout=-1), timeout=5.0) is False

    # 接続を開始していないため close() は無害
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_connect_success_with_timeout(test_certificates):
    """timeout を指定しても正常なハンドシェイクでは True を返すことを確認する"""
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    # 外側の wait_for は内側の connect(timeout=5.0) より大きくとり、
    # どちらのタイムアウトが先に発火するかの境界を分離する
    assert await asyncio.wait_for(client.connect(timeout=5.0), timeout=10.0) is True

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


def _server_remote_max_datagram_frame_size(server: Server) -> int | None:
    """サーバー側から観測したクライアントの DATAGRAM 広告サイズを返す

    サーバーの低レベル接続の remote_max_datagram_frame_size は、クライアントが
    広告した max_datagram_frame_size (RFC 9221 Section 3 の受信サポート広告) を
    返す。0 は「広告しない」を意味する。

    Returns:
        クライアントが広告した値。未接続なら None
    """
    for addr, connection in list(server._connections.items()):
        return connection.remote_max_datagram_frame_size
    return None


@pytest.mark.asyncio
async def test_max_datagram_frame_size_positive(test_certificates):
    """max_datagram_frame_size に正の値を指定すると DATAGRAM を広告することを確認する

    サーバー側の remote_max_datagram_frame_size が指定値になることで、クライアント
    の受信サポート広告を観測する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
        max_datagram_frame_size=1200,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    # サーバー側でクライアントの広告を観測できる
    for _ in range(50):
        advertised = _server_remote_max_datagram_frame_size(server)
        if advertised is not None:
            break
        await asyncio.sleep(0.05)
    assert advertised == 1200, "指定した max_datagram_frame_size が広告されるべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_max_datagram_frame_size_zero(test_certificates):
    """max_datagram_frame_size=0 では DATAGRAM を広告しないことを確認する

    サーバー側の remote_max_datagram_frame_size が 0 になる (広告しない) ことで
    クライアントの受信サポート広告が無いことを観測する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
        max_datagram_frame_size=0,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    # サーバー側でクライアントの広告が無いことを観測できる
    for _ in range(50):
        advertised = _server_remote_max_datagram_frame_size(server)
        if advertised is not None:
            break
        await asyncio.sleep(0.05)
    assert advertised == 0, "max_datagram_frame_size=0 では DATAGRAM を広告しないべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_max_datagram_frame_size_default(test_certificates):
    """max_datagram_frame_size 未指定 (None) では既定値で広告することを確認する

    既定 (enable_datagram=true / max_datagram_frame_size=65536) のまま広告し、
    サーバー側の remote_max_datagram_frame_size が 65536 になることで既存挙動を
    維持していることを観測する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    # サーバー側でクライアントの広告 (既定 65536) を観測できる
    for _ in range(50):
        advertised = _server_remote_max_datagram_frame_size(server)
        if advertised is not None:
            break
        await asyncio.sleep(0.05)
    assert advertised == 65536, "既定では 65536 で広告されるべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_max_datagram_frame_size_out_of_range(test_certificates):
    """max_datagram_frame_size に範囲外の値を指定すると ValueError になることを確認する

    負の値と変長整数上限 (2^62 - 1) を超える値を connect() が ValueError で
    拒否することを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    for bad_value in (-1, 2**62):
        client = Client(
            host="127.0.0.1",
            port=server.actual_port,
            verify_peer=False,
            max_datagram_frame_size=bad_value,
        )
        with pytest.raises(ValueError, match="max_datagram_frame_size must be in range"):
            await asyncio.wait_for(client.connect(), timeout=5.0)
        await client.close()

    await server.stop()


@pytest.mark.asyncio
async def test_max_datagram_frame_size_upper_boundary(test_certificates):
    """max_datagram_frame_size の上限境界値 (2^62 - 1) が受理されることを確認する

    変長整数上限 (RFC 9000 Section 16) の 2^62 - 1 が ValueError にならず、
    サーバー側の remote_max_datagram_frame_size で観測できることを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
        max_datagram_frame_size=2**62 - 1,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    for _ in range(50):
        advertised = _server_remote_max_datagram_frame_size(server)
        if advertised is not None:
            break
        await asyncio.sleep(0.05)
    assert advertised == 2**62 - 1, "上限境界値が広告されるべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_connect_timeout_connection_survives(test_certificates):
    """タイムアウト後も接続が存続し、後からハンドシェイクが完了できることを確認する

    connect(timeout=...) が False を返した後もバックグラウンド受信タスクは
    継続し、サーバーが応答を開始すればハンドシェイクが後追いで完了する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    # サーバーの run を起動せず、まずタイムアウトさせる
    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    connected = await asyncio.wait_for(client.connect(timeout=0.5), timeout=5.0)
    assert connected is False

    # タイムアウト後も _recv_task は生存している (接続は破棄されない)
    assert client._recv_task is not None
    assert not client._recv_task.done()

    # サーバーの run を後から起動すると、ハンドシェイクが後追いで完了する
    server_task = asyncio.create_task(_run_server(server))
    for _ in range(100):
        if client.is_connected:
            break
        await asyncio.sleep(0.05)
    assert client.is_connected is True, "後からハンドシェイクが完了するべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()
