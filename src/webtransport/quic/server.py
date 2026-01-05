"""QUIC サーバー

asyncio と UDP を使用した高レベル QUIC サーバー実装。
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

from webtransport.webtransport_ext import quic as quic_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Server:
    """QUIC サーバー

    asyncio を使用した非同期 QUIC サーバー。

    Usage:
        async with Server(host="0.0.0.0", port=4433) as server:
            await server.run()

        # または
        server = Server(host="0.0.0.0", port=4433)
        await server.start()
        await server.run()
        await server.stop()
    """

    def __init__(
        self,
        host: str,
        port: int,
        certfile: str | None = None,
        keyfile: str | None = None,
        alpn_protocols: list[str] | None = None,
        idle_timeout_ns: int = 30_000_000_000,
    ) -> None:
        """サーバーを初期化する

        Args:
            host: バインドするホストアドレス
            port: バインドするポート番号 (0 で自動割り当て)
            certfile: 証明書ファイルパス
            keyfile: 秘密鍵ファイルパス
            alpn_protocols: ALPN プロトコルリスト
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
        """
        self._host = host
        self._port = port
        self._certfile = certfile
        self._keyfile = keyfile
        self._alpn_protocols = alpn_protocols or ["h3"]
        self._idle_timeout_ns = idle_timeout_ns

        self._socket: socket.socket | None = None
        self._connections: dict[tuple[str, int], quic_low.Connection] = {}
        self._running = False
        self._actual_port = 0

        self._on_handshake_completed: Callable[[tuple[str, int]], Awaitable[None]] | None = None
        self._on_stream_data: (
            Callable[[int, bytes, bool, tuple[str, int]], Awaitable[None]] | None
        ) = None
        self._on_datagram: Callable[[bytes, tuple[str, int]], Awaitable[None]] | None = None
        self._on_connection_closed: Callable[[tuple[str, int]], Awaitable[None]] | None = None

    @property
    def host(self) -> str:
        """バインドしているホストアドレス"""
        return self._host

    @property
    def port(self) -> int:
        """指定されたポート番号"""
        return self._port

    @property
    def actual_port(self) -> int:
        """実際にバインドしているポート番号"""
        return self._actual_port

    @property
    def is_running(self) -> bool:
        """サーバーが実行中かどうか"""
        return self._running

    def on_handshake_completed(
        self,
        callback: Callable[[tuple[str, int]], Awaitable[None]],
    ) -> None:
        """ハンドシェイク完了時のコールバックを設定する

        Args:
            callback: async def callback(addr: tuple[str, int]) -> None
        """
        self._on_handshake_completed = callback

    def on_stream_data(
        self,
        callback: Callable[[int, bytes, bool, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """ストリームデータ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None
        """
        self._on_stream_data = callback

    def on_datagram(
        self,
        callback: Callable[[bytes, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """データグラム受信時のコールバックを設定する

        Args:
            callback: async def callback(data: bytes, addr: tuple[str, int]) -> None
        """
        self._on_datagram = callback

    def on_connection_closed(
        self,
        callback: Callable[[tuple[str, int]], Awaitable[None]],
    ) -> None:
        """接続終了時のコールバックを設定する

        Args:
            callback: async def callback(addr: tuple[str, int]) -> None
        """
        self._on_connection_closed = callback

    async def start(self) -> None:
        """サーバーを開始する"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind((self._host, self._port))
        self._actual_port = self._socket.getsockname()[1]
        self._running = True

    async def stop(self) -> None:
        """サーバーを停止する"""
        self._running = False
        for connection in self._connections.values():
            connection.close()
        self._connections.clear()
        if self._socket is not None:
            self._socket.close()
            self._socket = None

    async def __aenter__(self) -> Server:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.stop()

    def _create_config(self) -> quic_low.Config:
        """接続設定を作成する"""
        config = quic_low.Config()
        config.alpn_protocols = self._alpn_protocols
        config.idle_timeout_ns = self._idle_timeout_ns
        if self._certfile is not None:
            config.cert_file = self._certfile
        if self._keyfile is not None:
            config.key_file = self._keyfile
        return config

    def _accept_connection(
        self,
        addr: tuple[str, int],
        initial_packet: bytes,
    ) -> quic_low.Connection:
        """初期パケットから接続を作成する"""
        config = self._create_config()
        connection = quic_low.Connection.accept(config, initial_packet)
        connection.receive(initial_packet)
        self._connections[addr] = connection
        return connection

    async def _send_to(self, addr: tuple[str, int], connection: quic_low.Connection) -> None:
        """データを送信する"""
        if self._socket is None:
            return

        data = connection.send()
        if data:
            loop = asyncio.get_running_loop()
            await loop.sock_sendto(self._socket, data, addr)

    async def open_stream(
        self,
        addr: tuple[str, int],
        bidirectional: bool = True,
    ) -> int:
        """ストリームを開く

        Args:
            addr: クライアントアドレス
            bidirectional: 双方向ストリームかどうか

        Returns:
            ストリーム ID (-1 の場合は失敗)
        """
        connection = self._connections.get(addr)
        if connection is None:
            return -1

        return connection.open_stream(bidirectional)

    async def send_stream_data(
        self,
        addr: tuple[str, int],
        stream_id: int,
        data: bytes,
        fin: bool = False,
    ) -> None:
        """ストリームにデータを送信する

        Args:
            addr: クライアントアドレス
            stream_id: ストリーム ID
            data: 送信データ
            fin: ストリームを終了するか
        """
        connection = self._connections.get(addr)
        if connection is None:
            return

        connection.send_stream_data(stream_id, data, fin)
        await self._send_to(addr, connection)

    async def send_datagram(self, addr: tuple[str, int], data: bytes) -> None:
        """データグラムを送信する

        Args:
            addr: クライアントアドレス
            data: 送信データ
        """
        connection = self._connections.get(addr)
        if connection is None:
            return

        connection.send_datagram(data)
        await self._send_to(addr, connection)

    async def run(self) -> None:
        """メインループを実行する

        サーバーが停止されるまでブロックする。
        """
        if self._socket is None:
            raise RuntimeError("サーバーが開始されていません")

        loop = asyncio.get_running_loop()

        while self._running:
            try:
                data, addr = await asyncio.wait_for(
                    loop.sock_recvfrom(self._socket, 65535),
                    timeout=0.1,
                )

                if addr not in self._connections:
                    connection = self._accept_connection(addr, data)
                else:
                    connection = self._connections[addr]
                    connection.receive(data)

                while True:
                    event = connection.next_event()
                    if event is None:
                        break

                    if event.type == quic_low.EventType.HANDSHAKE_COMPLETED:
                        if self._on_handshake_completed is not None:
                            await self._on_handshake_completed(addr)

                    elif event.type == quic_low.EventType.STREAM_DATA:
                        if self._on_stream_data is not None:
                            await self._on_stream_data(
                                event.stream_id,
                                event.data,
                                event.fin,
                                addr,
                            )

                    elif event.type == quic_low.EventType.DATAGRAM:
                        if self._on_datagram is not None:
                            await self._on_datagram(event.data, addr)

                    elif event.type == quic_low.EventType.CONNECTION_CLOSED:
                        if self._on_connection_closed is not None:
                            await self._on_connection_closed(addr)
                        if addr in self._connections:
                            del self._connections[addr]

                await self._send_to(addr, connection)

            except TimeoutError:
                pass

            for addr, connection in list(self._connections.items()):
                timeout = connection.get_timeout()
                if timeout is not None and timeout <= 0:
                    connection.handle_timeout()
                    await self._send_to(addr, connection)

            await asyncio.sleep(0.001)
