"""QUIC クライアント

asyncio と UDP を使用した高レベル QUIC クライアント実装。
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING, Self

from webtransport.webtransport_ext import quic as quic_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


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
        ca_file: str | None = None,
        verify_callback: Callable[[list[bytes]], bool] | None = None,
        session_ticket: bytes | None = None,
        early_transport_params: bytes | None = None,
        enable_early_data: bool = True,
    ) -> None:
        """クライアントを初期化する

        Args:
            host: 接続先ホスト
            port: 接続先ポート
            alpn_protocols: ALPN プロトコルリスト
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
            verify_peer: ピア検証を行うか
            ca_file: CA 証明書ファイルパス
            verify_callback: ピア証明書検証コールバック
            session_ticket: 0-RTT 用セッションチケット (DER)
            early_transport_params: 0-RTT トランスポートパラメータ
            enable_early_data: 0-RTT early data を有効にするか
        """
        self._host = host
        self._port = port
        self._alpn_protocols = alpn_protocols or ["h3"]
        self._idle_timeout_ns = idle_timeout_ns
        self._verify_peer = verify_peer
        self._ca_file = ca_file
        self._verify_callback = verify_callback
        self._session_ticket = session_ticket
        self._early_transport_params = early_transport_params
        self._enable_early_data = enable_early_data

        self._connection: quic_low.Connection | None = None
        self._socket: socket.socket | None = None
        # bind 後のローカルアドレス (host, port)
        self._local_addr: tuple[str, int] | None = None
        self._running = False
        self._connected = False
        # SESSION_TICKET イベントで受け取った最新チケット
        self._latest_session_ticket: bytes | None = None

        self._on_handshake_completed: Callable[[], Awaitable[None]] | None = None
        self._on_stream_data: Callable[[int, bytes, bool], Awaitable[None]] | None = None
        self._on_datagram: Callable[[bytes], Awaitable[None]] | None = None
        self._on_connection_closed: Callable[[], Awaitable[None]] | None = None
        self._on_session_ticket: Callable[[bytes], Awaitable[None]] | None = None
        self._on_early_data_rejected: Callable[[], Awaitable[None]] | None = None

        # 0-RTT early data の送信待ちキュー (データ、fin)
        self._early_data_queue: list[tuple[bytes, bool]] = []

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

    def on_session_ticket(
        self,
        callback: Callable[[bytes], Awaitable[None]],
    ) -> None:
        """セッションチケット受信時のコールバックを設定する

        Args:
            callback: async def callback(ticket: bytes) -> None
        """
        self._on_session_ticket = callback

    def on_early_data_rejected(
        self,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        """0-RTT early data が拒否されたときのコールバックを設定する

        拒否された early data とそれに紐づくストリームの状態は破棄される
        (RFC 9001 Section 4.6.2。将来改訂される可能性がある)。再送する場合
        は呼び出し側でストリームを開き直してデータを送信する。

        Args:
            callback: async def callback() -> None
        """
        self._on_early_data_rejected = callback

    def register_early_data(self, data: bytes, fin: bool = False) -> None:
        """0-RTT として送信する early data を登録する

        connect() を呼び出す前に登録する。送出のタイミングと破棄の条件は
        _flush_early_data を参照。0-RTT はリプレイ攻撃のリスクがあるため
        (RFC 9001 Section 9.2。将来改訂される可能性がある)、冪等でない
        処理を early data として送信しないこと。

        Args:
            data: 送信データ
            fin: ストリームを終了するか

        Raises:
            RuntimeError: connect() の呼び出し後に登録しようとした場合
        """
        if self._connected:
            raise RuntimeError("early data must be registered before connect()")
        self._early_data_queue.append((data, fin))

    def _flush_early_data(self) -> None:
        """登録済みの early data を 0-RTT として送信待ちキューへ積む

        接続作成直後に呼び出し、ハンドシェイク完了前にストリームを開いて
        アプリケーションデータを 0-RTT パケットで送れるようにする。
        登録ごとに双方向ストリームを 1 本開いて送信する。0-RTT は
        session_ticket と 0-RTT トランスポートパラメータを指定した接続でのみ
        試行され (RFC 9000 Section 7.4.1。将来改訂される可能性がある)、
        試行されない接続ではストリームを開けない (open_stream が -1 を返す)
        ため送出されずに破棄される。破棄した項目があれば警告ログを出す。
        """
        if self._connection is None:
            return

        dropped = 0
        for data, fin in self._early_data_queue:
            stream_id = self._connection.open_stream(bidirectional=True)
            if stream_id < 0:
                dropped += 1
                continue
            self._connection.send_stream_data(stream_id, data, fin)
        if dropped > 0:
            logger.warning(
                "early data was not sent because a stream could not be opened "
                "before handshake completion (0-RTT not attempted or flow "
                "control limit reached): %d item(s)",
                dropped,
            )
        self._early_data_queue.clear()

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
        """パケットの送信先アドレスを決める

        パス情報が埋まっている場合はそれを使い、未設定なら接続先にフォールバックする。
        """
        if packet.remote_host and packet.remote_port:
            return (packet.remote_host, packet.remote_port)
        return (self._host, self._port)

    async def _send_pending(self) -> None:
        """送信待ちパケットを 1 つ送出する

        send() を ACK なしで連続 drain すると、ストリームデータ滞留時に
        戻ってこなくなるため、1 呼び出しあたり 1 パケットに留める。
        """
        if self._connection is None or self._socket is None:
            return

        packet = self._connection.send()
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
        if self._connection is None or self._socket is None:
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
            self._connection.receive(data, self._local_addr, remote)
        except TimeoutError:
            pass

    async def _handle_session_ticket_event(self, event: quic_low.Event) -> None:
        """SESSION_TICKET イベントを処理する"""
        self._latest_session_ticket = event.data
        if self._on_session_ticket is not None:
            await self._on_session_ticket(event.data)

    async def _handle_early_data_rejected_event(self) -> None:
        """EARLY_DATA_REJECTED イベントを処理する"""
        if self._on_early_data_rejected is not None:
            await self._on_early_data_rejected()

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
        config.enable_early_data = self._enable_early_data
        if self._ca_file is not None:
            config.ca_file = self._ca_file
        if self._verify_callback is not None:
            config.verify_callback = self._verify_callback
        if self._session_ticket is not None:
            config.session_ticket = self._session_ticket
        if self._early_transport_params is not None:
            config.early_transport_params = self._early_transport_params

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind(("0.0.0.0", 0))
        self._local_addr = self._normalize_addr(self._socket.getsockname())

        self._connection = quic_low.Connection.create_client(
            config,
            self._local_addr,
            (self._host, self._port),
        )
        self._flush_early_data()
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

                elif event.type == quic_low.EventType.SESSION_TICKET:
                    await self._handle_session_ticket_event(event)

                elif event.type == quic_low.EventType.EARLY_DATA_REJECTED:
                    await self._handle_early_data_rejected_event()

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

    def export_session_ticket(self) -> bytes:
        """セッションチケット (DER) を取得する

        Returns:
            セッションチケット。未取得の場合は空 bytes
        """
        if self._connection is None:
            return self._latest_session_ticket or b""
        ticket = self._connection.export_session_ticket()
        if ticket:
            self._latest_session_ticket = ticket
        return ticket or self._latest_session_ticket or b""

    def export_0rtt_transport_params(self) -> bytes:
        """0-RTT トランスポートパラメータを取得する

        Returns:
            トランスポートパラメータ。未取得の場合は空 bytes
        """
        if self._connection is None:
            return b""
        return self._connection.export_0rtt_transport_params()

    def is_early_data_accepted(self) -> bool:
        """0-RTT early data が受理されたか"""
        if self._connection is None:
            return False
        return self._connection.is_early_data_accepted()

    def was_early_data_attempted(self) -> bool:
        """0-RTT early data を試みたか"""
        if self._connection is None:
            return False
        return self._connection.was_early_data_attempted()

    async def migrate(self) -> bool:
        """ローカル UDP ソケットを差し替えてコネクションマイグレーションを開始する

        Returns:
            マイグレーション開始に成功した場合は True
        """
        if self._connection is None:
            return False

        new_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        new_socket.setblocking(False)
        new_socket.bind(("0.0.0.0", 0))
        new_local = self._normalize_addr(new_socket.getsockname())

        if not self._connection.initiate_migration(
            new_local,
            (self._host, self._port),
        ):
            new_socket.close()
            return False

        old_socket = self._socket
        self._socket = new_socket
        self._local_addr = new_local

        if old_socket is not None:
            old_socket.close()

        await self._send_pending()
        return True

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

                elif event.type == quic_low.EventType.SESSION_TICKET:
                    await self._handle_session_ticket_event(event)

                elif event.type == quic_low.EventType.EARLY_DATA_REJECTED:
                    await self._handle_early_data_rejected_event()

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

    async def __aenter__(self) -> Self:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.close()
