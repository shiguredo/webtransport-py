"""HTTP/2 サーバー

asyncio と TCP/TLS を使用した高レベル HTTP/2 サーバー実装。
"""

from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING

from webtransport.webtransport_ext import http2 as http2_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ResponseWriter:
    """HTTP/2 レスポンス送信用ヘルパー"""

    def __init__(
        self,
        writer: asyncio.StreamWriter,
        connection: http2_low.Connection,
    ) -> None:
        self._writer = writer
        self._connection = connection

    async def send_headers(
        self,
        stream_id: int,
        headers: list[tuple[str, str]],
    ) -> None:
        """レスポンスヘッダーを送信する"""
        self._connection.submit_response(stream_id, headers)
        data = self._connection.send()
        if data:
            self._writer.write(data)
            await self._writer.drain()

    async def send_data(
        self,
        stream_id: int,
        data: bytes,
        end_stream: bool = False,
    ) -> None:
        """データを送信する"""
        self._connection.send_data(stream_id, data, end_stream)
        send_data = self._connection.send()
        if send_data:
            self._writer.write(send_data)
            await self._writer.drain()


class Server:
    """HTTP/2 サーバー

    asyncio を使用した非同期 HTTP/2 サーバー。

    Usage:
        async with Server(host="0.0.0.0", port=8443, certfile="cert.pem", keyfile="key.pem") as server:
            server.on_request(handle_request)
            await server.run()
    """

    def __init__(
        self,
        host: str,
        port: int,
        certfile: str,
        keyfile: str,
    ) -> None:
        """サーバーを初期化する

        Args:
            host: バインドするホストアドレス
            port: バインドするポート番号 (0 で自動割り当て)
            certfile: 証明書ファイルパス
            keyfile: 秘密鍵ファイルパス
        """
        self._host = host
        self._port = port
        self._certfile = certfile
        self._keyfile = keyfile

        self._server: asyncio.Server | None = None
        self._running = False
        self._actual_port = 0

        self._on_request: (
            Callable[[int, list[tuple[str, str]], ResponseWriter], Awaitable[None]] | None
        ) = None
        self._on_data: Callable[[int, bytes, ResponseWriter], Awaitable[None]] | None = None

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

    def on_request(
        self,
        callback: Callable[[int, list[tuple[str, str]], ResponseWriter], Awaitable[None]],
    ) -> None:
        """リクエスト受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, headers: list[tuple[str, str]], response_writer: ResponseWriter) -> None
        """
        self._on_request = callback

    def on_data(
        self,
        callback: Callable[[int, bytes, ResponseWriter], Awaitable[None]],
    ) -> None:
        """データ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes, response_writer: ResponseWriter) -> None
        """
        self._on_data = callback

    async def start(self) -> None:
        """サーバーを開始する"""
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(self._certfile, self._keyfile)
        ssl_context.set_alpn_protocols(["h2"])

        self._server = await asyncio.start_server(
            self._handle_client,
            self._host,
            self._port,
            ssl=ssl_context,
        )

        sockets = self._server.sockets
        if sockets:
            self._actual_port = sockets[0].getsockname()[1]

        self._running = True

    async def stop(self) -> None:
        """サーバーを停止する"""
        self._running = False
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def __aenter__(self) -> Server:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.stop()

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """クライアント接続を処理する"""
        config = http2_low.Config()
        config.is_server = True
        connection = http2_low.Connection.create_server(config)

        response_writer = ResponseWriter(writer, connection)

        data = connection.send()
        if data:
            writer.write(data)
            await writer.drain()

        try:
            while True:
                try:
                    received = await asyncio.wait_for(reader.read(65535), timeout=0.1)
                    if not received:
                        break
                    connection.receive(received)
                except TimeoutError:
                    pass

                while True:
                    event = connection.next_event()
                    if event is None:
                        break

                    if event.type == http2_low.EventType.HEADERS:
                        if self._on_request is not None:
                            await self._on_request(event.stream_id, event.headers, response_writer)

                    elif event.type == http2_low.EventType.DATA:
                        if self._on_data is not None:
                            await self._on_data(event.stream_id, event.data, response_writer)

                    elif event.type == http2_low.EventType.GO_AWAY:
                        return

                data = connection.send()
                if data:
                    writer.write(data)
                    await writer.drain()

                if connection.is_closed():
                    break

                await asyncio.sleep(0.001)

        finally:
            writer.close()
            await writer.wait_closed()

    async def submit_response(
        self,
        writer: asyncio.StreamWriter,
        stream_id: int,
        headers: list[tuple[str, str]],
        connection: http2_low.Connection,
    ) -> None:
        """レスポンスヘッダーを送信する

        Args:
            writer: StreamWriter
            stream_id: ストリーム ID
            headers: レスポンスヘッダー
            connection: HTTP/2 接続
        """
        connection.submit_response(stream_id, headers)
        data = connection.send()
        if data:
            writer.write(data)
            await writer.drain()

    async def send_data(
        self,
        writer: asyncio.StreamWriter,
        stream_id: int,
        data: bytes,
        eof: bool,
        connection: http2_low.Connection,
    ) -> None:
        """ストリームにデータを送信する

        Args:
            writer: StreamWriter
            stream_id: ストリーム ID
            data: 送信データ
            eof: ストリームを終了するか
            connection: HTTP/2 接続
        """
        connection.send_data(stream_id, data, eof)
        send_data = connection.send()
        if send_data:
            writer.write(send_data)
            await writer.drain()

    async def run(self) -> None:
        """サーバーを実行する

        サーバーが停止されるまでブロックする。
        """
        if self._server is None:
            raise RuntimeError("サーバーが開始されていません")

        async with self._server:
            await self._server.serve_forever()
