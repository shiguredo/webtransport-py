"""WebTransport over HTTP/3 サーバー

asyncio と UDP を使用した高レベル WebTransport サーバー実装。
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

from webtransport import h3 as h3_low, quic

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ClientConnection:
    """クライアント接続を表すクラス"""

    def __init__(self) -> None:
        self.quic_connection: quic.Connection | None = None
        self.webtransport_session: h3_low.Session | None = None
        self.streams_setup: bool = False


class Server:
    """WebTransport over HTTP/3 サーバー

    asyncio を使用した非同期 WebTransport サーバー。

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
        idle_timeout_ns: int = 30_000_000_000,
    ) -> None:
        """サーバーを初期化する

        Args:
            host: バインドするホストアドレス
            port: バインドするポート番号 (0 で自動割り当て)
            certfile: 証明書ファイルパス
            keyfile: 秘密鍵ファイルパス
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
        """
        self._host = host
        self._port = port
        self._certfile = certfile
        self._keyfile = keyfile
        self._idle_timeout_ns = idle_timeout_ns

        self._socket: socket.socket | None = None
        self._clients: dict[tuple[str, int], ClientConnection] = {}
        self._running = False
        self._actual_port = 0

        self._on_session_ready: Callable[[int, tuple[str, int]], Awaitable[None]] | None = None
        self._on_session_closed: Callable[[int, tuple[str, int]], Awaitable[None]] | None = None
        self._on_stream_data: (
            Callable[[int, int, bytes, tuple[str, int]], Awaitable[None]] | None
        ) = None
        self._on_datagram: Callable[[int, bytes, tuple[str, int]], Awaitable[None]] | None = None

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
        callback: Callable[[int, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """セッション確立時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int, addr: tuple[str, int]) -> None
        """
        self._on_session_ready = callback

    def on_session_closed(
        self,
        callback: Callable[[int, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """セッション終了時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int, addr: tuple[str, int]) -> None
        """
        self._on_session_closed = callback

    def on_stream_data(
        self,
        callback: Callable[[int, int, bytes, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """ストリームデータ受信時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int, stream_id: int, data: bytes, addr: tuple[str, int]) -> None
        """
        self._on_stream_data = callback

    def on_datagram(
        self,
        callback: Callable[[int, bytes, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """データグラム受信時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int, data: bytes, addr: tuple[str, int]) -> None
        """
        self._on_datagram = callback

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
        for client in self._clients.values():
            if client.quic_connection is not None:
                client.quic_connection.close()
        self._clients.clear()
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

    def _create_connection(
        self,
        addr: tuple[str, int],
        initial_packet: bytes,
    ) -> ClientConnection:
        """新しいクライアント接続を作成する

        Args:
            addr: クライアントアドレス
            initial_packet: 最初に受信したパケット
        """
        client = ClientConnection()

        quic_config = quic.Config()
        quic_config.alpn_protocols = ["h3"]
        quic_config.idle_timeout_ns = self._idle_timeout_ns
        if self._certfile is not None:
            quic_config.cert_file = self._certfile
        if self._keyfile is not None:
            quic_config.key_file = self._keyfile

        webtransport_config = h3_low.Config()
        webtransport_config.is_server = True

        client.quic_connection = quic.Connection.accept(quic_config, initial_packet)
        client.quic_connection.receive(initial_packet)
        client.webtransport_session = h3_low.Session.create_server(webtransport_config)

        self._clients[addr] = client
        return client

    async def _send_to(self, addr: tuple[str, int], client: ClientConnection) -> None:
        """クライアントにデータを送信する"""
        if self._socket is None:
            return
        if client.quic_connection is None or client.webtransport_session is None:
            return

        for stream_id, stream_data, fin in client.webtransport_session.get_streams_to_send():
            client.quic_connection.send_stream_data(stream_id, stream_data, fin)

        for datagram in client.webtransport_session.get_datagrams_to_send():
            client.quic_connection.send_datagram(datagram)

        send_data = client.quic_connection.send()
        if send_data:
            loop = asyncio.get_running_loop()
            await loop.sock_sendto(self._socket, send_data, addr)

    def _setup_streams(self, client: ClientConnection) -> None:
        """HTTP/3 制御ストリームを設定する"""
        if client.quic_connection is None or client.webtransport_session is None:
            return
        if client.streams_setup:
            return

        control_stream_id = client.quic_connection.open_stream(False)
        client.webtransport_session.bind_control_stream(control_stream_id)

        encoder_stream_id = client.quic_connection.open_stream(False)
        client.webtransport_session.bind_qpack_encoder_stream(encoder_stream_id)

        decoder_stream_id = client.quic_connection.open_stream(False)
        client.webtransport_session.bind_qpack_decoder_stream(decoder_stream_id)

        # クライアントからの双方向ストリームを受け入れる準備
        client.webtransport_session.set_max_client_streams_bidi(100)

        client.streams_setup = True

    async def _process_quic_events(
        self,
        addr: tuple[str, int],
        client: ClientConnection,
    ) -> bool:
        """QUIC イベントを処理する

        Returns:
            接続が継続する場合は True、終了した場合は False
        """
        if client.quic_connection is None or client.webtransport_session is None:
            return False

        while True:
            quic_event = client.quic_connection.next_event()
            if quic_event is None:
                break

            if quic_event.type == quic.EventType.HANDSHAKE_COMPLETED:
                self._setup_streams(client)
            elif quic_event.type == quic.EventType.STREAM_DATA:
                client.webtransport_session.receive_stream_data(
                    quic_event.stream_id,
                    quic_event.data,
                    quic_event.fin,
                )
            elif quic_event.type == quic.EventType.DATAGRAM:
                client.webtransport_session.receive_datagram(quic_event.data)
            elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                return False

        return True

    async def _process_webtransport_events(
        self,
        addr: tuple[str, int],
        client: ClientConnection,
    ) -> None:
        """WebTransport イベントを処理する"""
        if client.webtransport_session is None:
            return

        while True:
            webtransport_event = client.webtransport_session.next_event()
            if webtransport_event is None:
                break

            if webtransport_event.type == h3_low.EventType.SESSION_READY:
                client.webtransport_session.accept_session(webtransport_event.session_id)
                if self._on_session_ready is not None:
                    await self._on_session_ready(webtransport_event.session_id, addr)

            elif webtransport_event.type == h3_low.EventType.SESSION_CLOSED:
                if self._on_session_closed is not None:
                    await self._on_session_closed(webtransport_event.session_id, addr)

            elif webtransport_event.type == h3_low.EventType.STREAM_DATA:
                if self._on_stream_data is not None:
                    await self._on_stream_data(
                        webtransport_event.session_id,
                        webtransport_event.stream_id,
                        webtransport_event.data,
                        addr,
                    )

            elif webtransport_event.type == h3_low.EventType.DATAGRAM:
                if self._on_datagram is not None:
                    session_ids = client.webtransport_session.get_session_ids()
                    session_id = session_ids[0] if session_ids else 0
                    await self._on_datagram(
                        session_id,
                        webtransport_event.data,
                        addr,
                    )

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
        client = self._clients.get(addr)
        if client is None or client.webtransport_session is None:
            return

        client.webtransport_session.send_stream_data(stream_id, data, fin)
        await self._send_to(addr, client)

    async def send_datagram(
        self,
        addr: tuple[str, int],
        session_id: int,
        data: bytes,
    ) -> None:
        """データグラムを送信する

        Args:
            addr: クライアントアドレス
            session_id: セッション ID
            data: 送信データ
        """
        client = self._clients.get(addr)
        if client is None or client.webtransport_session is None:
            return

        client.webtransport_session.send_datagram(session_id, data)
        await self._send_to(addr, client)

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

                if addr not in self._clients:
                    client = self._create_connection(addr, data)
                else:
                    client = self._clients[addr]
                    if client.quic_connection is not None:
                        client.quic_connection.receive(data)

                connection_alive = await self._process_quic_events(addr, client)
                if not connection_alive:
                    if addr in self._clients:
                        del self._clients[addr]
                    continue

                await self._process_webtransport_events(addr, client)
                await self._send_to(addr, client)

            except TimeoutError:
                pass

            for addr, client in list(self._clients.items()):
                if client.quic_connection is not None:
                    timeout = client.quic_connection.get_timeout()
                    if timeout is not None and timeout <= 0:
                        client.quic_connection.handle_timeout()
                        await self._send_to(addr, client)

            await asyncio.sleep(0.001)
