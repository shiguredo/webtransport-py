"""HTTP/2 サーバー

asyncio と TCP/TLS を使用した高レベル HTTP/2 サーバー実装。
"""

from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING, Self

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
        await self.drain()

    async def send_data(
        self,
        stream_id: int,
        data: bytes,
        end_stream: bool = False,
    ) -> None:
        """データを送信する"""
        self._connection.send_data(stream_id, data, end_stream)
        await self.drain()

    async def drain(self) -> None:
        """送信待ちのフレームを送出できるだけ送出する

        nghttp2_session_mem_send2 は 1 回の呼び出しで 1 フレームしか返さない
        ため、空が返るまで繰り返す (nghttp2.h の mem_send2 の説明に従う)。
        1 回で止めると 1 ループ 1 フレームに律速される。
        """
        while True:
            data = self._connection.send()
            if not data:
                return
            self._writer.write(data)
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
        self._on_stream_end: Callable[[int, ResponseWriter], Awaitable[None]] | None = None

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

    def on_stream_end(
        self,
        callback: Callable[[int, ResponseWriter], Awaitable[None]],
    ) -> None:
        """リクエストボディ終端 (END_STREAM) 受信時のコールバックを設定する

        POST などのリクエストボディの受信完了を検知してから応答を送るために
        使う。RESET_STREAM などで終了した場合は呼ばれない。

        Args:
            callback: async def callback(stream_id: int, response_writer: ResponseWriter) -> None
        """
        self._on_stream_end = callback

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
            # 処理中のクライアント transport を閉じてハンドラを起こす。
            # wait_closed() は全接続の終了を待つため、先に閉じないと
            # アクティブ接続がある限り復帰しない
            self._server.close_clients()
            self._server.close()
            await self._server.wait_closed()

    async def __aenter__(self) -> Self:
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

        await response_writer.drain()

        # 受信は常時 1 件だけ読み待ちにする。固定 0.1 秒タイムアウトで
        # 読むたびに待つ実装では、受信間隔がそのまま転送の律速になる
        # (フロー制御の更新が遅れて大容量レスポンスが極端に遅くなる)
        read_task: asyncio.Task[bytes] | None = asyncio.create_task(reader.read(65535))

        try:
            while self._running:
                assert read_task is not None
                done, _ = await asyncio.wait({read_task}, timeout=0.1)
                if done:
                    received = read_task.result()
                    read_task = None
                    if not received:
                        break
                    connection.receive(received)

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

                    elif event.type == http2_low.EventType.STREAM_END:
                        if self._on_stream_end is not None:
                            await self._on_stream_end(event.stream_id, response_writer)

                    elif event.type == http2_low.EventType.GO_AWAY:
                        # RFC 9113 Section 6.8 の graceful shutdown: GOAWAY
                        # 受信後も既存ストリームの送受信を継続する。接続終了
                        # はピアの接続クローズ (TCP EOF) と is_closed() で
                        # 検知する
                        pass

                await response_writer.drain()

                if connection.is_closed():
                    break

                if read_task is None:
                    read_task = asyncio.create_task(reader.read(65535))

                await asyncio.sleep(0.001)

        finally:
            if read_task is not None and not read_task.done():
                read_task.cancel()
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
        while True:
            data = connection.send()
            if not data:
                return
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
        while True:
            send_data = connection.send()
            if not send_data:
                return
            writer.write(send_data)
            await writer.drain()

    async def run(self) -> None:
        """サーバーを実行する

        サーバーが停止されるまでブロックする。
        """
        if self._server is None:
            raise RuntimeError("server is not started")

        async with self._server:
            await self._server.serve_forever()
