"""WebTransport over HTTP/3 クライアント

asyncio と UDP を使用した高レベル WebTransport クライアント実装。
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

from webtransport import h3 as h3_low, quic

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Client:
    """WebTransport over HTTP/3 クライアント

    asyncio を使用した非同期 WebTransport クライアント。

    Usage:
        client = Client(url="https://localhost:4433/webtransport")
        await client.connect()
        await client.send_datagram(b"Hello")
        await client.close()

        # または
        async with Client(url="https://localhost:4433/webtransport") as client:
            await client.send_datagram(b"Hello")
    """

    def __init__(
        self,
        url: str,
        verify_peer: bool = True,
        idle_timeout_ns: int = 30_000_000_000,
    ) -> None:
        """クライアントを初期化する

        Args:
            url: WebTransport エンドポイント URL
            verify_peer: サーバー証明書を検証するかどうか
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
        """
        self._url = url
        self._verify_peer = verify_peer
        self._idle_timeout_ns = idle_timeout_ns
        self._host, self._port, self._path = self._parse_url(url)

        self._quic_connection: quic.Connection | None = None
        self._webtransport_session: h3_low.Session | None = None
        self._socket: socket.socket | None = None
        self._running = False
        self._session_id = -1
        self._connected = False

        self._on_session_ready: Callable[[int], Awaitable[None]] | None = None
        self._on_session_closed: Callable[[int], Awaitable[None]] | None = None
        self._on_stream_data: Callable[[int, bytes], Awaitable[None]] | None = None
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
        if self._quic_connection is None:
            return
        if self._webtransport_session is None:
            return
        if self._socket is None:
            return

        for stream_id, stream_data, fin in self._webtransport_session.get_streams_to_send():
            self._quic_connection.send_stream_data(stream_id, stream_data, fin)

        for datagram in self._webtransport_session.get_datagrams_to_send():
            self._quic_connection.send_datagram(datagram)

        send_data = self._quic_connection.send()
        if send_data:
            loop = asyncio.get_running_loop()
            await loop.sock_sendto(self._socket, send_data, (self._host, self._port))

    async def _receive(self) -> None:
        """データを受信する"""
        if self._quic_connection is None or self._socket is None:
            return

        loop = asyncio.get_running_loop()
        try:
            data, _ = await asyncio.wait_for(
                loop.sock_recvfrom(self._socket, 65535),
                timeout=0.1,
            )
            self._quic_connection.receive(data)
        except TimeoutError:
            pass

    def _setup_streams(self) -> None:
        """HTTP/3 制御ストリームを設定する"""
        if self._quic_connection is None or self._webtransport_session is None:
            return

        control_stream_id = self._quic_connection.open_stream(False)
        self._webtransport_session.bind_control_stream(control_stream_id)

        encoder_stream_id = self._quic_connection.open_stream(False)
        self._webtransport_session.bind_qpack_encoder_stream(encoder_stream_id)

        decoder_stream_id = self._quic_connection.open_stream(False)
        self._webtransport_session.bind_qpack_decoder_stream(decoder_stream_id)

    async def connect(self) -> bool:
        """WebTransport セッションを確立する

        Returns:
            接続に成功した場合は True
        """
        quic_config = quic.Config()
        quic_config.alpn_protocols = ["h3"]
        quic_config.idle_timeout_ns = self._idle_timeout_ns
        quic_config.verify_peer = self._verify_peer
        quic_config.server_name = self._host

        webtransport_config = h3_low.Config()
        webtransport_config.is_server = False

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind(("0.0.0.0", 0))

        self._quic_connection = quic.Connection.create_client(quic_config)
        self._webtransport_session = h3_low.Session.create_client(webtransport_config)

        await self._send_pending()
        self._running = True

        handshake_done = False
        while not handshake_done and self._running:
            await self._receive()

            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break

                if quic_event.type == quic.EventType.HANDSHAKE_COMPLETED:
                    handshake_done = True
                    break
                elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                    self._running = False
                    return False

            await self._send_pending()
            await asyncio.sleep(0.01)

        self._setup_streams()
        await self._send_pending()

        # サーバーの SETTINGS を受信するまで待機
        settings_received = False
        max_attempts = 100
        attempt = 0
        while not settings_received and self._running and attempt < max_attempts:
            await self._receive()

            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break

                if quic_event.type == quic.EventType.STREAM_DATA:
                    # サーバーからの制御ストリームデータを処理
                    self._webtransport_session.receive_stream_data(
                        quic_event.stream_id,
                        quic_event.data,
                        quic_event.fin,
                    )
                    # サーバーの制御ストリーム (stream_id=3) からデータを受信したら設定完了とみなす
                    if quic_event.stream_id == 3:
                        settings_received = True
                elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                    self._running = False
                    return False

            await self._send_pending()
            await asyncio.sleep(0.01)
            attempt += 1

        if not settings_received:
            return False

        request_stream_id = self._quic_connection.open_stream(True)
        if self._webtransport_session.connect(request_stream_id, self._url):
            self._session_id = request_stream_id
            await self._send_pending()
            self._connected = True
            return True

        return False

    async def open_stream(self, unidirectional: bool = False) -> int:
        """WebTransport ストリームを開く

        Args:
            unidirectional: 単方向ストリームにするかどうか

        Returns:
            ストリーム ID
        """
        if self._quic_connection is None or self._webtransport_session is None:
            return -1

        stream_id = self._quic_connection.open_stream(not unidirectional)
        self._webtransport_session.open_stream(self._session_id, stream_id, unidirectional)
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
        if self._webtransport_session is None:
            return

        self._webtransport_session.send_stream_data(stream_id, data, fin)
        await self._send_pending()

    async def send_datagram(self, data: bytes) -> None:
        """データグラムを送信する

        Args:
            data: 送信データ
        """
        if self._webtransport_session is None:
            return

        self._webtransport_session.send_datagram(self._session_id, data)
        await self._send_pending()

    async def close_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームを閉じる

        Args:
            stream_id: ストリーム ID
            error_code: エラーコード
        """
        if self._webtransport_session is None:
            return

        self._webtransport_session.close_stream(stream_id, error_code)
        await self._send_pending()

    async def _process_quic_events(self) -> bool:
        """QUIC イベントを処理する

        Returns:
            接続が継続する場合は True
        """
        if self._quic_connection is None or self._webtransport_session is None:
            return False

        while True:
            quic_event = self._quic_connection.next_event()
            if quic_event is None:
                break

            if quic_event.type == quic.EventType.STREAM_DATA:
                self._webtransport_session.receive_stream_data(
                    quic_event.stream_id,
                    quic_event.data,
                    quic_event.fin,
                )
            elif quic_event.type == quic.EventType.DATAGRAM:
                self._webtransport_session.receive_datagram(quic_event.data)
            elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                return False

        return True

    async def _process_webtransport_events(self) -> None:
        """WebTransport イベントを処理する"""
        if self._webtransport_session is None:
            return

        while True:
            webtransport_event = self._webtransport_session.next_event()
            if webtransport_event is None:
                break

            if webtransport_event.type == h3_low.EventType.SESSION_READY:
                if self._on_session_ready is not None:
                    await self._on_session_ready(webtransport_event.session_id)

            elif webtransport_event.type == h3_low.EventType.SESSION_CLOSED:
                self._connected = False
                if self._on_session_closed is not None:
                    await self._on_session_closed(webtransport_event.session_id)

            elif webtransport_event.type == h3_low.EventType.STREAM_DATA:
                if self._on_stream_data is not None:
                    await self._on_stream_data(
                        webtransport_event.stream_id,
                        webtransport_event.data,
                    )

            elif webtransport_event.type == h3_low.EventType.DATAGRAM:
                if self._on_datagram is not None:
                    await self._on_datagram(webtransport_event.data)

    async def run(self) -> None:
        """メインループを実行する

        接続が終了するまでブロックする。
        """
        if self._quic_connection is None:
            raise RuntimeError("クライアントが接続されていません")

        while self._running:
            await self._receive()

            connection_alive = await self._process_quic_events()
            if not connection_alive:
                self._running = False
                break

            await self._process_webtransport_events()
            await self._send_pending()

            timeout = self._quic_connection.get_timeout()
            if timeout is not None and timeout <= 0:
                self._quic_connection.handle_timeout()

            await asyncio.sleep(0.01)

    async def close(self) -> None:
        """接続を閉じる"""
        self._running = False
        self._connected = False

        if self._webtransport_session is not None and self._session_id >= 0:
            self._webtransport_session.close_session(self._session_id)

        if self._quic_connection is not None:
            self._quic_connection.close()
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
