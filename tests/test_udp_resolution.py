"""UDP クライアントの名前解決テスト

localhost の解決順序によらず family 一致で接続できることを確認する
(実ソケットを使う。モックなし)。解決順自体は環境依存のため表明しない。
"""

from __future__ import annotations

import asyncio
import socket
import time

import pytest


def _first_family() -> socket.AddressFamily:
    """localhost 解決の先頭 family を返す"""
    infos = socket.getaddrinfo("localhost", 4433, type=socket.SOCK_DGRAM)
    return infos[0][0]


@pytest.mark.asyncio
async def test_quic_localhost_connects(test_certificates) -> None:
    """quic.Client が localhost で接続できる"""
    from webtransport.quic import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    try:
        # 解決順によらず family 一致で接続できる
        client = Client(
            host="localhost",
            port=server.actual_port,
            verify_peer=False,
        )
        connected = await asyncio.wait_for(client.connect(timeout=5.0), timeout=10.0)
        assert connected is True
        assert client.is_connected is True
        # 最終的にサーバー family (IPv4) で接続している
        assert client._socket is not None
        assert client._socket.family == socket.AF_INET
        await client.close()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_h3_localhost_connects(test_certificates) -> None:
    """h3.Client が localhost で接続できる"""
    from webtransport.h3 import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    try:
        # 解決順によらず family 一致で接続できる
        client = Client(
            url=f"https://localhost:{server.actual_port}/webtransport",
            verify_peer=False,
        )
        await client.connect(timeout=5.0)
        # 最終的にサーバー family (IPv4) で接続している
        assert client._socket is not None
        assert client._socket.family == socket.AF_INET

        async def run_client() -> None:
            try:
                await client.run()
            except asyncio.CancelledError:
                pass

        client_task = asyncio.create_task(run_client())
        try:
            assert client.is_connected is True
        finally:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
            await client.close()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_http3_localhost_connects(test_certificates) -> None:
    """http3.Client が localhost で接続できる"""
    from webtransport.http3 import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    try:
        # 解決順によらず family 一致で接続できる
        client = Client(
            host="localhost",
            port=server.actual_port,
            verify_peer=False,
        )
        await client.connect(timeout=5.0)
        # 最終的にサーバー family (IPv4) で接続している
        assert client._socket is not None
        assert client._socket.family == socket.AF_INET

        async def run_client() -> None:
            try:
                await client.run()
            except asyncio.CancelledError:
                pass

        client_task = asyncio.create_task(run_client())
        try:
            assert client.is_connected is True
        finally:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
            await client.close()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


def test_localhost_first_family_noted() -> None:
    """localhost 解決の先頭 family を記録する (順序自体は表明しない)"""
    # 環境依存の順序を可視化するための記録であり、成否判定には使わない
    assert _first_family() in (socket.AF_INET, socket.AF_INET6)


@pytest.mark.asyncio
async def test_localhost_fallback_exercised(test_certificates) -> None:
    """IPv6 先行環境でフォールバック経路を通ることを検証する"""
    # 決定的な検証には IPv6 先行の解決順が必要なため、それ以外の環境では
    # 飛ばす (順序自体は環境依存であり、通常テストは順序非依存で検証する)
    if _first_family() != socket.AF_INET6:
        pytest.skip("IPv6 先行の解決順でのみ検証できる")

    from webtransport.quic import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    try:
        # 先頭候補 (到達不能な IPv6) の打ち切りを待って次候補へ進むため、
        # 所要時間でフォールバックの実行を検証できる
        client = Client(
            host="localhost",
            port=server.actual_port,
            verify_peer=False,
        )
        start = time.monotonic()
        connected = await asyncio.wait_for(client.connect(timeout=5.0), timeout=15.0)
        elapsed = time.monotonic() - start
        assert connected is True
        assert elapsed >= 4.0
        assert client._socket is not None
        assert client._socket.family == socket.AF_INET
        await client.close()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()
