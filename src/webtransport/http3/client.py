"""HTTP/3 クライアント

asyncio と UDP を使用した高レベル HTTP/3 クライアント実装。
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING, Self

from webtransport.webtransport_ext import http3 as http3_low
from webtransport.webtransport_ext import quic as quic_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Client:
    """HTTP/3 クライアント

    asyncio を使用した非同期 HTTP/3 クライアント。

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
        idle_timeout_ns: int = 30_000_000_000,
        verify_peer: bool = True,
        ca_file: str | None = None,
        verify_callback: Callable[[list[bytes]], bool] | None = None,
    ) -> None:
        """クライアントを初期化する

        Args:
            host: 接続先ホスト
            port: 接続先ポート
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
            verify_peer: サーバー証明書を検証するかどうか
            ca_file: CA 証明書ファイルパス
            verify_callback: ピア証明書検証コールバック
        """
        self._host = host
        self._port = port
        self._idle_timeout_ns = idle_timeout_ns
        self._verify_peer = verify_peer
        self._ca_file = ca_file
        self._verify_callback = verify_callback

        self._quic_connection: quic_low.Connection | None = None
        self._http3_connection: http3_low.Connection | None = None
        self._socket: socket.socket | None = None
        # bind 後のローカルアドレス (host, port)
        self._local_addr: tuple[str, int] | None = None
        self._running = False
        self._connected = False
        self._control_stream_id = -1

        self._on_headers: Callable[[int, list[tuple[str, str]]], Awaitable[None]] | None = None
        self._on_data: Callable[[int, bytes], Awaitable[None]] | None = None
        self._on_stream_end: Callable[[int], Awaitable[None]] | None = None
        self._on_stream_reset: Callable[[int, int], Awaitable[None]] | None = None

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

    def on_stream_reset(
        self,
        callback: Callable[[int, int], Awaitable[None]],
    ) -> None:
        """ストリームリセット受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, error_code: int) -> None
        """
        self._on_stream_reset = callback

    def _normalize_addr(self, addr: tuple[object, ...]) -> tuple[str, int]:
        """recvfrom / getsockname のアドレスを (str, int) に正規化する"""
        host = addr[0]
        port = addr[1]
        if not isinstance(port, int):
            raise TypeError(f"expected port int, got {type(port).__name__}")
        return (str(host), port)

    def _destination_for_packet(
        self,
        packet: quic_low.Packet,
    ) -> tuple[str, int]:
        """パケットの送信先アドレスを決める"""
        if packet.remote_host and packet.remote_port:
            return (packet.remote_host, packet.remote_port)
        return (self._host, self._port)

    async def _send_pending(self) -> None:
        """送信待ちデータを送信する"""
        if self._quic_connection is None or self._http3_connection is None:
            return
        if self._socket is None:
            return

        for stream_id, stream_data, fin in self._http3_connection.get_streams_to_send():
            self._quic_connection.send_stream_data(stream_id, stream_data, fin)

        # send() の連続 drain は ACK 待ちが必要なケースでハングするため 1 パケットに留める
        packet = self._quic_connection.send()
        if packet is None:
            return

        loop = asyncio.get_running_loop()
        await loop.sock_sendto(
            self._socket,
            packet.data,
            self._destination_for_packet(packet),
        )

    async def _receive(self) -> None:
        """データを受信する"""
        if self._quic_connection is None or self._socket is None:
            return
        if self._local_addr is None:
            return

        loop = asyncio.get_running_loop()
        try:
            data, raw_remote = await asyncio.wait_for(
                loop.sock_recvfrom(self._socket, 65535),
                timeout=0.1,
            )
            remote = self._normalize_addr(raw_remote)
            self._quic_connection.receive(data, self._local_addr, remote)
        except TimeoutError:
            pass

    def _setup_http3_streams(self) -> None:
        """HTTP/3 制御ストリームを設定する"""
        if self._quic_connection is None or self._http3_connection is None:
            return

        if self._control_stream_id < 0:
            self._control_stream_id = self._quic_connection.open_stream(False)
            self._http3_connection.bind_control_stream(self._control_stream_id)

            encoder_stream_id = self._quic_connection.open_stream(False)
            self._http3_connection.bind_qpack_encoder_stream(encoder_stream_id)

            decoder_stream_id = self._quic_connection.open_stream(False)
            self._http3_connection.bind_qpack_decoder_stream(decoder_stream_id)

    async def connect(self) -> bool:
        """サーバーに接続する

        Returns:
            接続に成功した場合は True
        """
        quic_config = quic_low.Config()
        quic_config.alpn_protocols = ["h3"]
        quic_config.idle_timeout_ns = self._idle_timeout_ns
        quic_config.verify_peer = self._verify_peer
        quic_config.server_name = self._host
        if self._ca_file is not None:
            quic_config.ca_file = self._ca_file
        if self._verify_callback is not None:
            quic_config.verify_callback = self._verify_callback

        http3_config = http3_low.Config()
        http3_config.is_server = False

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind(("0.0.0.0", 0))
        self._local_addr = self._normalize_addr(self._socket.getsockname())

        self._quic_connection = quic_low.Connection.create_client(
            quic_config,
            self._local_addr,
            (self._host, self._port),
        )
        self._http3_connection = http3_low.Connection.create_client(http3_config)

        await self._send_pending()
        self._running = True

        while self._running:
            await self._receive()

            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break

                if quic_event.type == quic_low.EventType.HANDSHAKE_COMPLETED:
                    self._connected = True
                    self._setup_http3_streams()
                    await self._send_pending()
                    return True

                elif quic_event.type == quic_low.EventType.CONNECTION_CLOSED:
                    self._running = False
                    return False

            await self._send_pending()
            await asyncio.sleep(0.01)

        return False

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
        if self._quic_connection is None or self._http3_connection is None:
            return -1

        self._setup_http3_streams()

        stream_id = self._quic_connection.open_stream(True)

        request_headers: list[tuple[str, str]] = [
            (":method", method),
            (":path", path),
            (":scheme", "https"),
            (":authority", self._host),
        ]
        if headers is not None:
            request_headers.extend(headers)

        self._http3_connection.submit_request(stream_id, request_headers)
        await self._send_pending()
        return stream_id

    async def send_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """ストリームにデータを送信する

        Args:
            stream_id: ストリーム ID
            data: 送信データ
            fin: ストリームを終了するか
        """
        if self._http3_connection is None:
            return

        self._http3_connection.send_data(stream_id, data, fin)
        await self._send_pending()

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセットする (QUIC RESET_STREAM + nghttp3 通知)

        Args:
            stream_id: ストリーム ID
            error_code: エラーコード
        """
        if self._quic_connection is not None:
            self._quic_connection.reset_stream(stream_id, error_code)
        if self._http3_connection is not None:
            self._http3_connection.reset_stream(stream_id, error_code)
        await self._send_pending()

    async def run(self) -> None:
        """メインループを実行する

        接続が終了するまでブロックする。
        """
        if self._quic_connection is None or self._http3_connection is None:
            raise RuntimeError("クライアントが接続されていません")

        while self._running:
            await self._receive()

            # 受信 FIN が立った双方向ストリーム (STREAM_END 通知用)
            finished_streams: list[int] = []

            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break

                if quic_event.type == quic_low.EventType.STREAM_DATA:
                    self._http3_connection.receive_stream_data(
                        quic_event.stream_id,
                        quic_event.data,
                        quic_event.fin,
                    )
                    # nghttp3_conn_close_stream は大きな応答受信中に
                    # 残りの DATA イベントを落とすことがあるため使わない。
                    # QUIC の FIN をストリーム終端の合図として使う。
                    if quic_event.fin and quic_event.stream_id % 4 in (0, 1):
                        finished_streams.append(quic_event.stream_id)
                elif quic_event.type == quic_low.EventType.STREAM_RESET:
                    if self._on_stream_reset is not None:
                        await self._on_stream_reset(
                            quic_event.stream_id,
                            quic_event.error_code,
                        )
                elif quic_event.type == quic_low.EventType.CONNECTION_CLOSED:
                    self._running = False
                    self._connected = False

            while True:
                http3_event = self._http3_connection.next_event()
                if http3_event is None:
                    break

                if http3_event.type == http3_low.EventType.HEADERS:
                    if self._on_headers is not None:
                        await self._on_headers(http3_event.stream_id, http3_event.headers)

                elif http3_event.type == http3_low.EventType.DATA:
                    if self._on_data is not None:
                        await self._on_data(http3_event.stream_id, http3_event.data)

                elif http3_event.type == http3_low.EventType.STREAM_END:
                    if self._on_stream_end is not None:
                        await self._on_stream_end(http3_event.stream_id)

                elif http3_event.type in (
                    http3_low.EventType.RESET_STREAM,
                    http3_low.EventType.RESET,
                ):
                    self._quic_connection.reset_stream(
                        http3_event.stream_id,
                        http3_event.error_code,
                    )

                elif http3_event.type == http3_low.EventType.STOP_SENDING:
                    self._quic_connection.stop_sending(
                        http3_event.stream_id,
                        http3_event.error_code,
                    )

            # HTTP/3 の DATA を処理したあとに STREAM_END を通知する
            if self._on_stream_end is not None:
                for stream_id in finished_streams:
                    await self._on_stream_end(stream_id)

            await self._send_pending()

            timeout = self._quic_connection.get_timeout()
            if timeout is not None and timeout <= 0:
                self._quic_connection.handle_timeout()

            await asyncio.sleep(0.01)

    async def close(self) -> None:
        """接続を閉じる"""
        self._running = False
        self._connected = False

        if self._quic_connection is not None:
            self._quic_connection.close()
            await self._send_pending()

        if self._socket is not None:
            self._socket.close()
            self._socket = None

    async def __aenter__(self) -> Self:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.close()
