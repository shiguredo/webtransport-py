"""WebTransport over HTTP/2 サーバー

asyncio と TCP/TLS を使用した高レベル WebTransport サーバー実装。
Capsule Protocol (RFC 9297) を使用して WebTransport ストリームと DATAGRAM をサポート。
"""

from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING

from webtransport.webtransport_ext import h2 as h2_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class SessionWriter:
    """WebTransport セッションへのデータ送信用ヘルパー"""

    def __init__(
        self,
        writer: asyncio.StreamWriter,
        session: h2_low.Session,
        session_id: int,
    ) -> None:
        self._writer = writer
        self._session = session
        self._session_id = session_id

    @property
    def session_id(self) -> int:
        """セッション ID"""
        return self._session_id

    async def open_stream(self, unidirectional: bool = False) -> int:
        """ストリームを開く

        Args:
            unidirectional: 単方向ストリームにするかどうか

        Returns:
            ストリーム ID
        """
        return self._session.open_stream(self._session_id, unidirectional)

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
        self._session.send_stream_data(self._session_id, stream_id, data, fin)
        send_data = self._session.send()
        if send_data:
            self._writer.write(send_data)
            await self._writer.drain()

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセットする

        Args:
            stream_id: ストリーム ID
            error_code: エラーコード
        """
        self._session.reset_stream(self._session_id, stream_id, error_code)
        send_data = self._session.send()
        if send_data:
            self._writer.write(send_data)
            await self._writer.drain()


class Server:
    """WebTransport over HTTP/2 サーバー

    asyncio を使用した非同期 WebTransport サーバー。

    Usage:
        async with Server(host="0.0.0.0", port=8443, certfile="cert.pem", keyfile="key.pem") as server:
            server.on_session_ready(handle_session)
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

        self._on_session_ready: Callable[[SessionWriter], Awaitable[None]] | None = None
        self._on_session_closed: Callable[[SessionWriter], Awaitable[None]] | None = None
        self._on_stream_data: Callable[[int, bytes, SessionWriter], Awaitable[None]] | None = None

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

    def on_session_ready(
        self,
        callback: Callable[[SessionWriter], Awaitable[None]],
    ) -> None:
        """セッション確立時のコールバックを設定する

        Args:
            callback: async def callback(session_writer: SessionWriter) -> None
        """
        self._on_session_ready = callback

    def on_session_closed(
        self,
        callback: Callable[[SessionWriter], Awaitable[None]],
    ) -> None:
        """セッション終了時のコールバックを設定する

        Args:
            callback: async def callback(session_writer: SessionWriter) -> None
        """
        self._on_session_closed = callback

    def on_stream_data(
        self,
        callback: Callable[[int, bytes, SessionWriter], Awaitable[None]],
    ) -> None:
        """ストリームデータ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes, session_writer: SessionWriter) -> None
        """
        self._on_stream_data = callback

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
        config = h2_low.Config()
        config.is_server = True
        session = h2_low.Session.create_server(config)

        session_writers: dict[int, SessionWriter] = {}

        data = session.send()
        if data:
            writer.write(data)
            await writer.drain()

        try:
            while True:
                try:
                    received = await asyncio.wait_for(reader.read(65535), timeout=0.1)
                    if not received:
                        break
                    session.receive(received)
                except TimeoutError:
                    pass

                while True:
                    event = session.next_event()
                    if event is None:
                        break

                    if event.type == h2_low.EventType.SESSION_READY:
                        session.accept_session(event.session_id)
                        session_writer = SessionWriter(writer, session, event.session_id)
                        session_writers[event.session_id] = session_writer
                        if self._on_session_ready is not None:
                            await self._on_session_ready(session_writer)

                    elif event.type == h2_low.EventType.SESSION_CLOSED:
                        session_writer = session_writers.get(event.session_id)
                        if session_writer is not None and self._on_session_closed is not None:
                            await self._on_session_closed(session_writer)
                        if event.session_id in session_writers:
                            del session_writers[event.session_id]

                    elif event.type == h2_low.EventType.STREAM_DATA:
                        session_writer = session_writers.get(event.session_id)
                        if session_writer is not None and self._on_stream_data is not None:
                            await self._on_stream_data(
                                event.stream_id,
                                event.data,
                                session_writer,
                            )

                data = session.send()
                if data:
                    writer.write(data)
                    await writer.drain()

                if session.is_closed():
                    break

                await asyncio.sleep(0.001)

        finally:
            writer.close()
            await writer.wait_closed()

    async def run(self) -> None:
        """サーバーを実行する

        サーバーが停止されるまでブロックする。
        """
        if self._server is None:
            raise RuntimeError("サーバーが開始されていません")

        async with self._server:
            await self._server.serve_forever()
