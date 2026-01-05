"""HTTP/3 サーバー

asyncio と UDP を使用した高レベル HTTP/3 サーバー実装。
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING

from webtransport.webtransport_ext import http3 as http3_low, quic as quic_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class ClientConnection:
    """クライアント接続を表すクラス"""

    def __init__(self) -> None:
        self.quic_connection: quic_low.Connection | None = None
        self.http3_connection: http3_low.Connection | None = None
        self.control_stream_id: int = -1
        self.qpack_encoder_stream_id: int = -1
        self.qpack_decoder_stream_id: int = -1
        self.http3_streams_setup: bool = False

    def setup_http3_streams(self) -> None:
        """HTTP/3 制御ストリームとQPACKストリームを設定する"""
        if self.quic_connection is None or self.http3_connection is None:
            return
        if self.http3_streams_setup:
            return

        # サーバー側の制御ストリーム（単方向）を開く
        self.control_stream_id = self.quic_connection.open_stream(bidirectional=False)
        if self.control_stream_id < 0:
            # ハンドシェイクが完了していない場合はスキップ
            return
        self.http3_connection.bind_control_stream(self.control_stream_id)

        # QPACK エンコーダーストリーム（単方向）を開く
        self.qpack_encoder_stream_id = self.quic_connection.open_stream(bidirectional=False)
        if self.qpack_encoder_stream_id < 0:
            return
        self.http3_connection.bind_qpack_encoder_stream(self.qpack_encoder_stream_id)

        # QPACK デコーダーストリーム（単方向）を開く
        self.qpack_decoder_stream_id = self.quic_connection.open_stream(bidirectional=False)
        if self.qpack_decoder_stream_id < 0:
            return
        self.http3_connection.bind_qpack_decoder_stream(self.qpack_decoder_stream_id)

        self.http3_streams_setup = True


class Server:
    """HTTP/3 サーバー

    asyncio を使用した非同期 HTTP/3 サーバー。

    Usage:
        async with Server(host="0.0.0.0", port=4433) as server:
            server.on_request(handle_request)
            await server.run()
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

        self._on_request: (
            Callable[[int, list[tuple[str, str]], tuple[str, int]], Awaitable[None]] | None
        ) = None
        self._on_data: Callable[[int, bytes, tuple[str, int]], Awaitable[None]] | None = None

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
        callback: Callable[[int, list[tuple[str, str]], tuple[str, int]], Awaitable[None]],
    ) -> None:
        """リクエスト受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, headers: list[tuple[str, str]], addr: tuple[str, int]) -> None
        """
        self._on_request = callback

    def on_data(
        self,
        callback: Callable[[int, bytes, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """データ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes, addr: tuple[str, int]) -> None
        """
        self._on_data = callback

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

    def _accept_connection(
        self,
        addr: tuple[str, int],
        initial_packet: bytes,
    ) -> ClientConnection:
        """初期パケットから新しいクライアント接続を作成する"""
        client = ClientConnection()

        quic_config = quic_low.Config()
        quic_config.alpn_protocols = ["h3"]
        quic_config.idle_timeout_ns = self._idle_timeout_ns
        if self._certfile is not None:
            quic_config.cert_file = self._certfile
        if self._keyfile is not None:
            quic_config.key_file = self._keyfile

        http3_config = http3_low.Config()
        http3_config.is_server = True

        client.quic_connection = quic_low.Connection.accept(quic_config, initial_packet)
        client.quic_connection.receive(initial_packet)
        client.http3_connection = http3_low.Connection.create_server(http3_config)

        self._clients[addr] = client
        return client

    async def _send_to(self, addr: tuple[str, int], client: ClientConnection) -> None:
        """クライアントにデータを送信する"""
        if self._socket is None:
            return
        if client.quic_connection is None or client.http3_connection is None:
            return

        for stream_id, stream_data, fin in client.http3_connection.get_streams_to_send():
            client.quic_connection.send_stream_data(stream_id, stream_data, fin)

        send_data = client.quic_connection.send()
        if send_data:
            loop = asyncio.get_running_loop()
            await loop.sock_sendto(self._socket, send_data, addr)

    async def submit_response(
        self,
        addr: tuple[str, int],
        stream_id: int,
        headers: list[tuple[str, str]],
    ) -> None:
        """レスポンスヘッダーを送信する

        Args:
            addr: クライアントアドレス
            stream_id: ストリーム ID
            headers: レスポンスヘッダー
        """
        client = self._clients.get(addr)
        if client is None or client.http3_connection is None:
            return

        client.http3_connection.submit_response(stream_id, headers)
        await self._send_to(addr, client)

    async def send_data(
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
        if client is None or client.http3_connection is None:
            return

        client.http3_connection.send_data(stream_id, data, fin)
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
                    client = self._accept_connection(addr, data)
                else:
                    client = self._clients[addr]
                    if client.quic_connection is not None:
                        client.quic_connection.receive(data)

                if client.quic_connection is None or client.http3_connection is None:
                    continue

                while True:
                    quic_event = client.quic_connection.next_event()
                    if quic_event is None:
                        break

                    if quic_event.type == quic_low.EventType.STREAM_DATA:
                        client.http3_connection.receive_stream_data(
                            quic_event.stream_id,
                            quic_event.data,
                            quic_event.fin,
                        )
                    elif quic_event.type == quic_low.EventType.CONNECTION_CLOSED:
                        if addr in self._clients:
                            del self._clients[addr]
                        continue

                # ハンドシェイク完了後に HTTP/3 ストリームを設定
                client.setup_http3_streams()

                while True:
                    http3_event = client.http3_connection.next_event()
                    if http3_event is None:
                        break

                    if http3_event.type == http3_low.EventType.HEADERS:
                        if self._on_request is not None:
                            await self._on_request(
                                http3_event.stream_id,
                                http3_event.headers,
                                addr,
                            )

                    elif http3_event.type == http3_low.EventType.DATA:
                        if self._on_data is not None:
                            await self._on_data(
                                http3_event.stream_id,
                                http3_event.data,
                                addr,
                            )

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
