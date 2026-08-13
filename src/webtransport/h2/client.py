"""WebTransport over HTTP/2 クライアント

asyncio と TCP/TLS を使用した高レベル WebTransport クライアント実装。
Capsule Protocol (RFC 9297) を使用して WebTransport ストリームと DATAGRAM をサポート。
"""

from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING, Self

from webtransport.webtransport_ext import h2 as h2_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Client:
    """WebTransport over HTTP/2 クライアント

    asyncio を使用した非同期 WebTransport クライアント。

    Usage:
        client = Client(url="https://localhost:8443/webtransport")
        await client.connect()
        stream_id = await client.open_stream()
        await client.send_stream_data(stream_id, b"Hello")
        await client.send_datagram(b"Hello DG")
        await client.run()
        await client.close()

        # または
        async with Client(url="https://localhost:8443/webtransport") as client:
            stream_id = await client.open_stream()
            await client.send_stream_data(stream_id, b"Hello")
            await client.run()
    """

    def __init__(
        self,
        url: str,
        verify_peer: bool = True,
        origin: str = "",
    ) -> None:
        """クライアントを初期化する

        Args:
            url: WebTransport エンドポイント URL
            verify_peer: サーバー証明書を検証するかどうか
            origin: Origin ヘッダー値 (空なら付与しない)
        """
        self._url = url
        self._host, self._port, self._path = self._parse_url(url)
        self._verify_peer = verify_peer
        self._origin = origin

        self._session: h2_low.Session | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._running = False
        self._connected = False
        self._session_id = -1

        self._on_session_ready: Callable[[int], Awaitable[None]] | None = None
        self._on_session_closed: Callable[[int], Awaitable[None]] | None = None
        self._on_stream_data: Callable[[int, bytes], Awaitable[None]] | None = None
        self._on_stream_reset: Callable[[int, int], Awaitable[None]] | None = None
        self._on_datagram: Callable[[bytes], Awaitable[None]] | None = None

    @property
    def url(self) -> str:
        """接続先 URL"""
        return self._url

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
        """WebTransport セッションが確立しているかどうか"""
        return self._connected

    @property
    def session_id(self) -> int:
        """セッション ID"""
        return self._session_id

    def on_session_ready(
        self,
        callback: Callable[[int], Awaitable[None]],
    ) -> None:
        """セッション確立時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int) -> None
        """
        self._on_session_ready = callback

    def on_session_closed(
        self,
        callback: Callable[[int], Awaitable[None]],
    ) -> None:
        """セッション終了時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int) -> None
        """
        self._on_session_closed = callback

    def on_stream_data(
        self,
        callback: Callable[[int, bytes], Awaitable[None]],
    ) -> None:
        """ストリームデータ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes) -> None
        """
        self._on_stream_data = callback

    def on_stream_reset(
        self,
        callback: Callable[[int, int], Awaitable[None]],
    ) -> None:
        """ストリームリセット受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, error_code: int) -> None
        """
        self._on_stream_reset = callback

    def on_datagram(
        self,
        callback: Callable[[bytes], Awaitable[None]],
    ) -> None:
        """データグラム受信時のコールバックを設定する

        Args:
            callback: async def callback(data: bytes) -> None
        """
        self._on_datagram = callback

    def _parse_url(self, url: str) -> tuple[str, int, str]:
        """URL をパースする"""
        url = url.replace("https://", "")
        if "/" in url:
            host_port, path = url.split("/", 1)
            path = "/" + path
        else:
            host_port = url
            path = "/"

        if ":" in host_port:
            host, port_str = host_port.split(":")
            port = int(port_str)
        else:
            host = host_port
            port = 443

        return host, port, path

    async def _send_pending(self) -> None:
        """送信待ちデータを送信する"""
        if self._session is None or self._writer is None:
            return

        data = self._session.send()
        if data:
            self._writer.write(data)
            await self._writer.drain()

    async def _receive(self) -> None:
        """データを受信する"""
        if self._session is None or self._reader is None:
            return

        try:
            data = await asyncio.wait_for(self._reader.read(65535), timeout=0.1)
            if data:
                self._session.receive(data)
            else:
                self._running = False
        except TimeoutError:
            pass

    async def _wait_webtransport_ready(self, timeout_seconds: float = 5.0) -> bool:
        """対向の SETTINGS を待ち WebTransport が利用可能になるまで待機する

        draft-15 Section 3.1: ENABLE_CONNECT_PROTOCOL と WT_ENABLED を
        受信するまで CONNECT してはならない。
        """
        if self._session is None:
            return False

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if self._session.is_webtransport_ready():
                return True
            await self._receive()
            await self._send_pending()
            await asyncio.sleep(0.001)
        return False

    async def connect(self) -> bool:
        """WebTransport セッションを確立する

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

        config = h2_low.Config()
        config.is_server = False
        self._session = h2_low.Session.create_client(config)

        await self._send_pending()

        self._running = True

        # draft-15 Section 3.1: SETTINGS 受信後に Extended CONNECT を送る
        if not await self._wait_webtransport_ready():
            return False

        self._session_id = self._session.connect(self._url, self._origin)
        if self._session_id < 0:
            return False

        await self._send_pending()

        # 200 OK レスポンスを待つ
        while self._running:
            await self._receive()
            await self._send_pending()

            while True:
                event = self._session.next_event()
                if event is None:
                    break

                if (
                    event.type == h2_low.EventType.SESSION_READY
                    and event.session_id == self._session_id
                ):
                    self._connected = True
                    return True

                if (
                    event.type == h2_low.EventType.SESSION_CLOSED
                    and event.session_id == self._session_id
                ):
                    self._connected = False
                    return False

            await asyncio.sleep(0.001)

        return False

    async def open_stream(self, unidirectional: bool = False) -> int:
        """WebTransport ストリームを開く

        Args:
            unidirectional: 単方向ストリームにするかどうか

        Returns:
            ストリーム ID
        """
        if self._session is None or self._session_id < 0:
            return -1

        stream_id = self._session.open_stream(self._session_id, unidirectional)
        await self._send_pending()
        return stream_id

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
        if self._session is None or self._session_id < 0:
            return

        self._session.send_stream_data(self._session_id, stream_id, data, fin)
        await self._send_pending()

    async def send_datagram(self, data: bytes) -> None:
        """データグラムを送信する

        Capsule Protocol の DATAGRAM capsule (RFC 9297) を使う。
        draft-15 Section 6.11

        終了したセッション ID (WT_CLOSE_SESSION 受信後 / close_session 後 /
        ピアの END_STREAM 受信後) への送信は無視される (draft-15 Section 3.4
        のセッション終了。仕様強制ではなく実装ポリシー)。

        Args:
            data: 送信データ
        """
        if self._session is None or self._session_id < 0:
            return

        self._session.send_datagram(self._session_id, data)
        await self._send_pending()

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセットする

        Args:
            stream_id: ストリーム ID
            error_code: エラーコード
        """
        if self._session is None or self._session_id < 0:
            return

        self._session.reset_stream(self._session_id, stream_id, error_code)
        await self._send_pending()

    async def run(self) -> None:
        """メインループを実行する

        接続が終了するまでブロックする。
        """
        if self._session is None:
            raise RuntimeError("クライアントが接続されていません")

        while self._running:
            await self._receive()
            await self._send_pending()

            while True:
                event = self._session.next_event()
                if event is None:
                    break

                if (
                    event.type == h2_low.EventType.SESSION_READY
                    and self._on_session_ready is not None
                ):
                    await self._on_session_ready(event.session_id)

                elif event.type == h2_low.EventType.SESSION_CLOSED:
                    self._connected = False
                    if self._on_session_closed is not None:
                        await self._on_session_closed(event.session_id)

                elif (
                    event.type == h2_low.EventType.STREAM_DATA and self._on_stream_data is not None
                ):
                    await self._on_stream_data(event.stream_id, event.data)

                elif (
                    event.type == h2_low.EventType.STREAM_RESET
                    and self._on_stream_reset is not None
                ):
                    await self._on_stream_reset(event.stream_id, event.error_code)

                elif event.type == h2_low.EventType.DATAGRAM and self._on_datagram is not None:
                    await self._on_datagram(event.data)

            if self._session.is_closed():
                self._running = False

            await asyncio.sleep(0.01)

    async def close(self) -> None:
        """接続を閉じる

        draft-15 Section 6.12: WT_CLOSE_SESSION 後に CONNECT ストリームを
        half-close する。
        """
        was_running = self._running
        self._running = False
        self._connected = False

        if self._session is not None and self._session_id >= 0:
            self._session.close_session(self._session_id)
            await self._send_pending()
            # run() が並行していないときだけ half-close 完了を待つ
            if not was_running:
                for _ in range(10):
                    await self._receive()
                    await self._send_pending()
                    await asyncio.sleep(0.01)

        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except ssl.SSLError, ConnectionError, OSError:
                pass

    async def __aenter__(self) -> Self:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.close()
