"""HTTP/2 クライアント

asyncio と TCP/TLS を使用した高レベル HTTP/2 クライアント実装。
"""

from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING

from webtransport.webtransport_ext import http2 as http2_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Client:
    """HTTP/2 クライアント

    asyncio を使用した非同期 HTTP/2 クライアント。

    Usage:
        client = Client(host="example.com", port=443)
        await client.connect()
        stream_id = await client.request("GET", "/")
        await client.run()
        await client.close()

        # または
        async with Client(host="example.com", port=443) as client:
            stream_id = await client.request("GET", "/")
            await client.run()
    """

    def __init__(
        self,
        host: str,
        port: int = 443,
        verify_peer: bool = True,
    ) -> None:
        """クライアントを初期化する

        Args:
            host: 接続先ホスト
            port: 接続先ポート
            verify_peer: サーバー証明書を検証するかどうか
        """
        self._host = host
        self._port = port
        self._verify_peer = verify_peer

        self._connection: http2_low.Connection | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._running = False
        self._connected = False

        self._on_headers: Callable[[int, list[tuple[str, str]]], Awaitable[None]] | None = None
        self._on_data: Callable[[int, bytes], Awaitable[None]] | None = None
        self._on_stream_end: Callable[[int], Awaitable[None]] | None = None

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

    def on_headers(
        self,
        callback: Callable[[int, list[tuple[str, str]]], Awaitable[None]],
    ) -> None:
        """ヘッダー受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, headers: list[tuple[str, str]]) -> None
        """
        self._on_headers = callback

    def on_data(
        self,
        callback: Callable[[int, bytes], Awaitable[None]],
    ) -> None:
        """データ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes) -> None
        """
        self._on_data = callback

    def on_stream_end(
        self,
        callback: Callable[[int], Awaitable[None]],
    ) -> None:
        """ストリーム終了時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int) -> None
        """
        self._on_stream_end = callback

    async def _send_pending(self) -> None:
        """送信待ちデータを送信する"""
        if self._connection is None or self._writer is None:
            return

        data = self._connection.send()
        if data:
            self._writer.write(data)
            await self._writer.drain()

    async def _receive(self) -> None:
        """データを受信する"""
        if self._connection is None or self._reader is None:
            return

        try:
            data = await asyncio.wait_for(self._reader.read(65535), timeout=0.1)
            if data:
                self._connection.receive(data)
            else:
                self._running = False
        except TimeoutError:
            pass

    async def connect(self) -> bool:
        """サーバーに接続する

        Returns:
            接続に成功した場合は True
        """
        if self._verify_peer:
            ssl_context = ssl.create_default_context()
        else:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        ssl_context.set_alpn_protocols(["h2"])

        self._reader, self._writer = await asyncio.open_connection(
            self._host,
            self._port,
            ssl=ssl_context,
        )

        config = http2_low.Config()
        config.is_server = False
        self._connection = http2_low.Connection.create_client(config)

        await self._send_pending()

        self._running = True
        self._connected = True
        return True

    async def request(
        self,
        method: str,
        path: str,
        headers: list[tuple[str, str]] | None = None,
    ) -> int:
        """HTTP リクエストを送信する

        Args:
            method: HTTP メソッド
            path: リクエストパス
            headers: 追加のヘッダー

        Returns:
            ストリーム ID
        """
        if self._connection is None:
            return -1

        request_headers: list[tuple[str, str]] = [
            (":method", method),
            (":path", path),
            (":scheme", "https"),
            (":authority", self._host),
        ]
        if headers is not None:
            request_headers.extend(headers)

        stream_id = self._connection.submit_request(request_headers)
        await self._send_pending()
        return stream_id

    async def send_data(self, stream_id: int, data: bytes, eof: bool = False) -> None:
        """ストリームにデータを送信する

        Args:
            stream_id: ストリーム ID
            data: 送信データ
            eof: ストリームを終了するか
        """
        if self._connection is None:
            return

        self._connection.send_data(stream_id, data, eof)
        await self._send_pending()

    async def run(self) -> None:
        """メインループを実行する

        接続が終了するまでブロックする。
        """
        if self._connection is None:
            raise RuntimeError("クライアントが接続されていません")

        while self._running:
            await self._receive()
            await self._send_pending()

            while True:
                event = self._connection.next_event()
                if event is None:
                    break

                if event.type == http2_low.EventType.HEADERS:
                    if self._on_headers is not None:
                        await self._on_headers(event.stream_id, event.headers)

                elif event.type == http2_low.EventType.DATA:
                    if self._on_data is not None:
                        await self._on_data(event.stream_id, event.data)

                elif event.type == http2_low.EventType.STREAM_END:
                    if self._on_stream_end is not None:
                        await self._on_stream_end(event.stream_id)

                elif event.type == http2_low.EventType.GO_AWAY:
                    self._running = False

            await asyncio.sleep(0.01)

    async def close(self) -> None:
        """接続を閉じる"""
        self._running = False
        self._connected = False

        if self._connection is not None:
            self._connection.goaway()
            await self._send_pending()

        if self._writer is not None:
            self._writer.close()
            await self._writer.wait_closed()

    async def __aenter__(self) -> Client:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.close()
