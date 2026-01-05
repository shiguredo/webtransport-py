"""QUIC クライアント

asyncio と UDP を使用した高レベル QUIC クライアント実装。
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

from webtransport.webtransport_ext import quic as quic_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Client:
    """QUIC クライアント

    asyncio を使用した非同期 QUIC クライアント。

    Usage:
        client = Client(host="localhost", port=4433)
        await client.connect()
        stream_id = await client.open_stream()
        await client.send_stream_data(stream_id, b"Hello")
        await client.close()

        # または
        async with Client(host="localhost", port=4433) as client:
            stream_id = await client.open_stream()
            await client.send_stream_data(stream_id, b"Hello")
    """

    def __init__(
        self,
        host: str,
        port: int,
        alpn_protocols: list[str] | None = None,
        idle_timeout_ns: int = 30_000_000_000,
        verify_peer: bool = True,
    ) -> None:
        """クライアントを初期化する

        Args:
            host: 接続先ホスト
            port: 接続先ポート
            alpn_protocols: ALPN プロトコルリスト
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
            verify_peer: ピア検証を行うか
        """
        self._host = host
        self._port = port
        self._alpn_protocols = alpn_protocols or ["h3"]
        self._idle_timeout_ns = idle_timeout_ns
        self._verify_peer = verify_peer

        self._connection: quic_low.Connection | None = None
        self._socket: socket.socket | None = None
        self._running = False
        self._connected = False

        self._on_handshake_completed: Callable[[], Awaitable[None]] | None = None
        self._on_stream_data: Callable[[int, bytes, bool], Awaitable[None]] | None = None
        self._on_datagram: Callable[[bytes], Awaitable[None]] | None = None
        self._on_connection_closed: Callable[[], Awaitable[None]] | None = None

    @property
    def host(self) -> str:
        """接続先ホスト"""
        return self._host

    @property
    def port(self) -> int:
        """接続先ポート"""
        return self._port

    @property
    def is_connected(self) -> bool:
        """接続が確立しているかどうか"""
        return self._connected

    def on_handshake_completed(
        self,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        """ハンドシェイク完了時のコールバックを設定する

        Args:
            callback: async def callback() -> None
        """
        self._on_handshake_completed = callback

    def on_stream_data(
        self,
        callback: Callable[[int, bytes, bool], Awaitable[None]],
    ) -> None:
        """ストリームデータ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes, fin: bool) -> None
        """
        self._on_stream_data = callback

    def on_datagram(
        self,
        callback: Callable[[bytes], Awaitable[None]],
    ) -> None:
        """データグラム受信時のコールバックを設定する

        Args:
            callback: async def callback(data: bytes) -> None
        """
        self._on_datagram = callback

    def on_connection_closed(
        self,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        """接続終了時のコールバックを設定する

        Args:
            callback: async def callback() -> None
        """
        self._on_connection_closed = callback

    async def _send_pending(self) -> None:
        """送信待ちデータを送信する"""
        if self._connection is None or self._socket is None:
            return

        data = self._connection.send()
        if data:
            loop = asyncio.get_running_loop()
            await loop.sock_sendto(self._socket, data, (self._host, self._port))

    async def _receive(self) -> None:
        """データを受信する"""
        if self._connection is None or self._socket is None:
            return

        loop = asyncio.get_running_loop()
        try:
            data, _ = await asyncio.wait_for(
                loop.sock_recvfrom(self._socket, 65535),
                timeout=0.1,
            )
            self._connection.receive(data)
        except TimeoutError:
            pass

    async def connect(self) -> bool:
        """サーバーに接続する

        Returns:
            接続に成功した場合は True
        """
        config = quic_low.Config()
        config.alpn_protocols = self._alpn_protocols
        config.idle_timeout_ns = self._idle_timeout_ns
        config.verify_peer = self._verify_peer
        config.server_name = self._host

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind(("0.0.0.0", 0))

        self._connection = quic_low.Connection.create_client(config)
        await self._send_pending()
        self._running = True

        while self._running:
            await self._receive()

            while True:
                event = self._connection.next_event()
                if event is None:
                    break

                if event.type == quic_low.EventType.HANDSHAKE_COMPLETED:
                    self._connected = True
                    if self._on_handshake_completed is not None:
                        await self._on_handshake_completed()
                    await self._send_pending()
                    return True

                elif event.type == quic_low.EventType.CONNECTION_CLOSED:
                    self._running = False
                    return False

            await self._send_pending()
            await asyncio.sleep(0.01)

        return False

    async def open_stream(self, bidirectional: bool = True) -> int:
        """ストリームを開く

        Args:
            bidirectional: 双方向ストリームにするかどうか

        Returns:
            ストリーム ID
        """
        if self._connection is None:
            return -1

        return self._connection.open_stream(bidirectional)

    async def send_stream_data(
        self,
        stream_id: int,
        data: bytes,
        fin: bool = False,
    ) -> None:
        """ストリームにデータを送信する

        Args:
            stream_id: ストリーム ID
            data: 送信データ
            fin: ストリームを終了するか
        """
        if self._connection is None:
            return

        self._connection.send_stream_data(stream_id, data, fin)
        await self._send_pending()

    async def send_datagram(self, data: bytes) -> None:
        """データグラムを送信する

        Args:
            data: 送信データ
        """
        if self._connection is None:
            return

        self._connection.send_datagram(data)
        await self._send_pending()

    async def run(self) -> None:
        """メインループを実行する

        接続が終了するまでブロックする。
        """
        if self._connection is None:
            raise RuntimeError("クライアントが接続されていません")

        while self._running:
            await self._receive()

            while True:
                event = self._connection.next_event()
                if event is None:
                    break

                if event.type == quic_low.EventType.STREAM_DATA:
                    if self._on_stream_data is not None:
                        await self._on_stream_data(
                            event.stream_id,
                            event.data,
                            event.fin,
                        )

                elif event.type == quic_low.EventType.DATAGRAM:
                    if self._on_datagram is not None:
                        await self._on_datagram(event.data)

                elif event.type == quic_low.EventType.CONNECTION_CLOSED:
                    self._running = False
                    self._connected = False
                    if self._on_connection_closed is not None:
                        await self._on_connection_closed()

            await self._send_pending()

            timeout = self._connection.get_timeout()
            if timeout is not None and timeout <= 0:
                self._connection.handle_timeout()

            await asyncio.sleep(0.01)

    async def close(self) -> None:
        """接続を閉じる"""
        self._running = False
        self._connected = False

        if self._connection is not None:
            self._connection.close()
            await self._send_pending()

        if self._socket is not None:
            self._socket.close()
            self._socket = None

    async def __aenter__(self) -> Client:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.close()
