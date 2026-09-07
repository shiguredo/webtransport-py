"""connect() のロス回復テスト

UDP リレー器具で最初の 1 パケットを落とし、PTO 再送による回復を検証する
(実ソケットを使う。モックなし)。
"""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest


class UdpRelay:
    """双方向 UDP リレー (最初の中継 1 発を落とす)

    クライアントとサーバーの間に立ち、クライアント方向の最初の 1 発だけ
    落とす。落としたことと中継したことを数える。
    """

    def __init__(self, server_addr: tuple[str, int]) -> None:
        """リレー用ソケットを用意する"""
        self._server_addr = server_addr
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind(("127.0.0.1", 0))
        self._client_addr: tuple[str, int] | None = None
        self.attempted = 0
        self.forwarded = 0
        self._dropped = False

    @property
    def addr(self) -> tuple[str, int]:
        """リレーのアドレス (クライアントの接続先)"""
        return self._socket.getsockname()  # type: ignore[no-any-return]

    async def run(self) -> None:
        """中継ループを実行する。終了は cancel する"""
        loop = asyncio.get_running_loop()
        while True:
            data, raw_addr = await loop.sock_recvfrom(self._socket, 65535)
            src = (str(raw_addr[0]), int(raw_addr[1]))
            if src == self._server_addr:
                # サーバー方向はそのまま中継する
                if self._client_addr is not None:
                    await loop.sock_sendto(self._socket, data, self._client_addr)
            else:
                # クライアント方向は最初だけ数えて落とす
                if self._client_addr is None:
                    self._client_addr = src
                self.attempted += 1
                if not self._dropped:
                    self._dropped = True
                    continue
                self.forwarded += 1
                await loop.sock_sendto(self._socket, data, self._server_addr)

    def close(self) -> None:
        """リレー用ソケットを閉じる"""
        self._socket.close()


@asynccontextmanager
async def _relayed_server(server: Any) -> AsyncIterator[UdpRelay]:
    """サーバーとリレーを起動し、後始末付きでリレーを返す"""
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    relay = UdpRelay(("127.0.0.1", server.actual_port))
    relay_task = asyncio.create_task(relay.run())
    try:
        yield relay
    finally:
        relay_task.cancel()
        await asyncio.gather(relay_task, return_exceptions=True)
        relay.close()
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_h3_connect_recovers_single_loss(test_certificates) -> None:
    """h3 の connect() が 1 パケットのロスから回復する"""
    from webtransport.h3 import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    async with _relayed_server(server) as relay:
        # リレー経由で接続する (最初の 1 発が落ちる)
        client = Client(
            url=f"https://127.0.0.1:{relay.addr[1]}/webtransport",
            verify_peer=False,
        )
        await client.connect(timeout=6.0)

        # 落とした分だけ試行が中継を上回り、回復している
        assert relay.attempted > relay.forwarded
        assert relay.forwarded >= 1
        await client.close()


@pytest.mark.asyncio
async def test_http3_connect_recovers_single_loss(test_certificates) -> None:
    """http3 の connect() が 1 パケットのロスから回復する"""
    from webtransport.http3 import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    async with _relayed_server(server) as relay:
        # リレー経由で接続する (最初の 1 発が落ちる)
        client = Client(
            host="127.0.0.1",
            port=relay.addr[1],
            verify_peer=False,
        )
        await client.connect(timeout=6.0)

        # 落とした分だけ試行が中継を上回り、回復している
        assert relay.attempted > relay.forwarded
        assert relay.forwarded >= 1
        await client.close()


@pytest.mark.asyncio
async def test_http3_connect_blackhole_timeout() -> None:
    """http3 の connect() が無応答宛先で期限切れになる"""
    from webtransport.exceptions import ConnectTimeoutError
    from webtransport.http3 import Client

    # 応答を返さない宛先に接続する
    client = Client(
        host="10.255.255.1",
        port=443,
        verify_peer=False,
    )
    try:
        # 期限超過後の初回反復で ConnectTimeoutError になる
        start = time.monotonic()
        with pytest.raises(ConnectTimeoutError):
            await client.connect(timeout=1.0)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.9
        assert elapsed < 10.0
        assert client.is_connected is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_http3_connect_refused_invalid_host() -> None:
    """http3 の connect() が名前解決失敗で拒否される"""
    from webtransport.exceptions import ConnectRefusedError
    from webtransport.http3 import Client

    # 解決不能な宛先に接続する (名前解決失敗は生成失敗として送出され、
    # ConnectRefusedError に寄せられる)
    client = Client(
        host="nonexistent.invalid",
        port=443,
        verify_peer=False,
    )
    try:
        with pytest.raises(ConnectRefusedError) as exc_info:
            await client.connect(timeout=6.0)
        assert isinstance(exc_info.value.__cause__, OSError)
        assert client.is_connected is False
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_h3_connect_refused_invalid_host() -> None:
    """h3 の connect() が名前解決失敗で拒否される"""
    from webtransport.exceptions import ConnectRefusedError
    from webtransport.h3 import Client

    # 解決不能な宛先に接続する (http3 と対称の振る舞い)
    client = Client(
        url="https://nonexistent.invalid:443/webtransport",
        verify_peer=False,
    )
    try:
        with pytest.raises(ConnectRefusedError) as exc_info:
            await client.connect(timeout=6.0)
        assert isinstance(exc_info.value.__cause__, OSError)
        assert client.is_connected is False
    finally:
        await client.close()
