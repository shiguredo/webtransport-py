"""QUIC サーバー

asyncio と UDP を使用した高レベル QUIC サーバー実装。
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


class Server:
    """QUIC サーバー

    asyncio を使用した非同期 QUIC サーバー。

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
        alpn_protocols: list[str] | None = None,
        idle_timeout_ns: int = 30_000_000_000,
    ) -> None:
        """サーバーを初期化する

        Args:
            host: バインドするホストアドレス
            port: バインドするポート番号 (0 で自動割り当て)
            certfile: 証明書ファイルパス
            keyfile: 秘密鍵ファイルパス
            alpn_protocols: ALPN プロトコルリスト
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
        """
        self._host = host
        self._port = port
        self._certfile = certfile
        self._keyfile = keyfile
        self._alpn_protocols = alpn_protocols or ["h3"]
        self._idle_timeout_ns = idle_timeout_ns

        self._socket: socket.socket | None = None
        # bind 後のローカルアドレス (host, port)
        self._local_addr: tuple[str, int] | None = None
        self._connections: dict[tuple[str, int], quic_low.Connection] = {}
        # DCID から接続を引く索引 (未知アドレスからの short header 用)。
        # RFC 9000 Section 5.2 に従い DCID での照合を試みる。自側の発行
        # CID は 8 バイト固定のため short header の [1:9] で照合する
        # (zero-length CID の例外は非該当)。試し受信の O(N) 走査は行わず、
        # 索引照会と張り替えは O(1) である
        self._dcid_index: dict[bytes, quic_low.Connection] = {}
        # 接続ごとの発行済み SCID 集合 (自側 SCID = ピアから見た DCID)。
        # 索引の更新と破棄に使う
        self._conn_dcids: dict[quic_low.Connection, set[bytes]] = {}
        # 接続からアドレスを引く逆引き (キー張り替えを O(1) にする)。
        # Connection は identity hash のためキーに使える
        self._conn_addr: dict[quic_low.Connection, tuple[str, int]] = {}
        self._running = False
        self._actual_port = 0

        self._on_handshake_completed: Callable[[tuple[str, int]], Awaitable[None]] | None = None
        self._on_stream_data: (
            Callable[[int, bytes, bool, tuple[str, int]], Awaitable[None]] | None
        ) = None
        self._on_datagram: Callable[[bytes, tuple[str, int]], Awaitable[None]] | None = None
        self._on_connection_closed: Callable[[tuple[str, int]], Awaitable[None]] | None = None

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

    def on_handshake_completed(
        self,
        callback: Callable[[tuple[str, int]], Awaitable[None]],
    ) -> None:
        """ハンドシェイク完了時のコールバックを設定する

        Args:
            callback: async def callback(addr: tuple[str, int]) -> None
        """
        self._on_handshake_completed = callback

    def on_stream_data(
        self,
        callback: Callable[[int, bytes, bool, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """ストリームデータ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None
        """
        self._on_stream_data = callback

    def on_datagram(
        self,
        callback: Callable[[bytes, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """データグラム受信時のコールバックを設定する

        Args:
            callback: async def callback(data: bytes, addr: tuple[str, int]) -> None
        """
        self._on_datagram = callback

    def on_connection_closed(
        self,
        callback: Callable[[tuple[str, int]], Awaitable[None]],
    ) -> None:
        """接続終了時のコールバックを設定する

        Args:
            callback: async def callback(addr: tuple[str, int]) -> None
        """
        self._on_connection_closed = callback

    def _normalize_addr(self, addr: tuple[object, ...]) -> tuple[str, int]:
        """recvfrom / getsockname のアドレスを (str, int) に正規化する"""
        host = addr[0]
        port = addr[1]
        if not isinstance(port, int):
            raise TypeError(f"expected port int, got {type(port).__name__}")
        return (str(host), port)

    async def start(self) -> None:
        """サーバーを開始する"""
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind((self._host, self._port))
        self._local_addr = self._normalize_addr(self._socket.getsockname())
        self._actual_port = self._local_addr[1]
        self._running = True

    async def stop(self) -> None:
        """サーバーを停止する"""
        self._running = False
        try:
            for addr, connection in list(self._connections.items()):
                connection.close()
                try:
                    # close() が生成した CONNECTION_CLOSE をピアへ送出する。
                    # 1 接続の送出失敗で残りの接続への送出が中断されないよう
                    # 接続ごとに例外を隔離する
                    await self._send_to(addr, connection)
                except OSError as exc:
                    logger.warning("failed to send connection close: %s", exc)
        finally:
            self._connections.clear()
            self._dcid_index.clear()
            self._conn_dcids.clear()
            self._conn_addr.clear()
            if self._socket is not None:
                self._socket.close()
                self._socket = None

    async def __aenter__(self) -> Self:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.stop()

    def _create_config(self) -> quic_low.Config:
        """接続設定を作成する"""
        config = quic_low.Config()
        config.alpn_protocols = self._alpn_protocols
        config.idle_timeout_ns = self._idle_timeout_ns
        if self._certfile is not None:
            config.cert_file = self._certfile
        if self._keyfile is not None:
            config.key_file = self._keyfile
        return config

    def _accept_connection(
        self,
        addr: tuple[str, int],
        initial_packet: bytes,
    ) -> quic_low.Connection:
        """初期パケットから接続を作成する"""
        if self._local_addr is None:
            raise RuntimeError("server is not started")

        config = self._create_config()
        connection = quic_low.Connection.accept(
            config,
            initial_packet,
            self._local_addr,
            addr,
        )
        connection.receive(initial_packet, self._local_addr, addr)
        self._connections[addr] = connection
        self._conn_addr[connection] = addr
        self._refresh_dcid_index(connection)
        return connection

    def _refresh_dcid_index(self, connection: quic_low.Connection) -> None:
        """接続の発行済み SCID を索引に反映する

        自側 SCID (= ピアから見た DCID) の新規発行分を登録し、退役で消えた分を破棄する。退役 CID の索引残り
        は無害である (受信結果で破棄と判定され張り替えない) が、接触の
        たびに最新化して残存期間を抑える。8 バイト以外の CID は索引の
        照会形式と合わないため登録しない (現行は 8 バイトに揃う。初期
        SCID 長起点であり、将来の非 8 バイトは安全側に破棄される)。
        """
        # 未登録の接続には何もしない (除去直後の再登録を防ぐ防御)
        if connection not in self._conn_addr:
            return
        current = {cid for cid in connection.scid if len(cid) == 8}
        previous = self._conn_dcids.get(connection, set())
        for retired in previous - current:
            if self._dcid_index.get(retired) is connection:
                del self._dcid_index[retired]
        for issued in current - previous:
            self._dcid_index[issued] = connection
        self._conn_dcids[connection] = current

    def _drop_dcid_index(self, connection: quic_low.Connection) -> None:
        """接続の索引登録を全て破棄する"""
        for cid in self._conn_dcids.pop(connection, set()):
            if self._dcid_index.get(cid) is connection:
                del self._dcid_index[cid]

    def _remove_connection(self, connection: quic_low.Connection) -> None:
        """接続の登録を全て外す (アドレス・逆引き・索引)"""
        old_addr = self._conn_addr.pop(connection, None)
        if old_addr is not None and self._connections.get(old_addr) is connection:
            del self._connections[old_addr]
        self._drop_dcid_index(connection)

    async def _drain_connection_events(
        self, addr: tuple[str, int], connection: quic_low.Connection
    ) -> None:
        """受信後のイベントを処理する。終了時は登録を外す"""
        while True:
            event = connection.next_event()
            if event is None:
                break

            if event.type == quic_low.EventType.HANDSHAKE_COMPLETED:
                if self._on_handshake_completed is not None:
                    await self._on_handshake_completed(addr)

            elif event.type == quic_low.EventType.STREAM_DATA:
                if self._on_stream_data is not None:
                    await self._on_stream_data(
                        event.stream_id,
                        event.data,
                        event.fin,
                        addr,
                    )

            elif event.type == quic_low.EventType.DATAGRAM:
                if self._on_datagram is not None:
                    await self._on_datagram(event.data, addr)

            elif event.type == quic_low.EventType.CONNECTION_CLOSED:
                if self._on_connection_closed is not None:
                    await self._on_connection_closed(addr)
                self._remove_connection(connection)

    async def _send_to(self, addr: tuple[str, int], connection: quic_low.Connection) -> None:
        """接続の送信待ちパケットを 1 つ送出する

        パケットにリモートアドレスが埋まっていればそれを使い、
        未設定ならマップ上のクライアントアドレスにフォールバックする。
        send() の連続 drain は ACK 待ちが必要なケースでハングするため行わない。
        """
        if self._socket is None:
            return

        packet = connection.send()
        if packet is None:
            return

        if packet.remote_host and packet.remote_port:
            dest: tuple[str, int] = (packet.remote_host, packet.remote_port)
        else:
            dest = addr

        loop = asyncio.get_running_loop()
        await loop.sock_sendto(self._socket, packet.data, dest)

        # 送信経路の CID 発行に追従するため、送出後に索引を最新化する
        self._refresh_dcid_index(connection)

    async def open_stream(
        self,
        addr: tuple[str, int],
        bidirectional: bool = True,
    ) -> int:
        """ストリームを開く

        Args:
            addr: クライアントアドレス
            bidirectional: 双方向ストリームかどうか

        Returns:
            ストリーム ID (-1 の場合は失敗)
        """
        connection = self._connections.get(addr)
        if connection is None:
            return -1

        return connection.open_stream(bidirectional)

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
        connection = self._connections.get(addr)
        if connection is None:
            return

        connection.send_stream_data(stream_id, data, fin)
        await self._send_to(addr, connection)

    async def send_datagram(self, addr: tuple[str, int], data: bytes) -> None:
        """データグラムを送信する

        Args:
            addr: クライアントアドレス
            data: 送信データ
        """
        connection = self._connections.get(addr)
        if connection is None:
            return

        connection.send_datagram(data)
        await self._send_to(addr, connection)

    async def run(self) -> None:
        """メインループを実行する

        サーバーが停止されるまでブロックする。
        """
        if self._socket is None or self._local_addr is None:
            raise RuntimeError("server is not started")

        loop = asyncio.get_running_loop()

        while self._running:
            try:
                data, raw_addr = await asyncio.wait_for(
                    loop.sock_recvfrom(self._socket, 65535),
                    timeout=0.1,
                )
                addr = self._normalize_addr(raw_addr)

                connection = self._connections.get(addr)
                if connection is None:
                    # Connection Migration 後は送信元ポートが変わる。
                    # Short header は DCID 索引で既存接続を O(1) で引く
                    # (RFC 9000 Section 5.2 に従い DCID での照合を試みる)。
                    # Long header (Initial 等) は新規 accept する。
                    is_long_header = bool(data) and (data[0] & 0x80) != 0
                    if not is_long_header and len(data) >= 9:
                        # DCID は short header の [1:9] にある 8 バイト固定
                        # とする (自側の発行 CID は初期 SCID 長に揃う)。
                        # 一致しなければ破棄し、試し受信の走査は行わない
                        candidate = self._dcid_index.get(bytes(data[1:9]))
                        if candidate is not None:
                            result = candidate.receive(
                                data,
                                self._local_addr,
                                addr,
                            )
                            if result == quic_low.ReceiveResult.ACCEPTED:
                                # 正当な Migration としてアドレスキーを張り替える
                                old_addr = self._conn_addr.get(candidate)
                                if old_addr != addr:
                                    if (
                                        old_addr is not None
                                        and self._connections.get(old_addr) is candidate
                                    ):
                                        del self._connections[old_addr]
                                    self._connections[addr] = candidate
                                    self._conn_addr[candidate] = addr
                                self._refresh_dcid_index(candidate)
                                connection = candidate
                            elif result == quic_low.ReceiveResult.CLOSED:
                                # 終了時は後処理 (イベント drain と登録外し)
                                # のために drain する。通知先は登録済みの旧
                                # アドレスにする。破棄時は何もしない
                                # (滞留イベントの誤帰属を防ぐ)
                                await self._drain_connection_events(
                                    self._conn_addr.get(candidate, addr), candidate
                                )
                    if connection is None:
                        try:
                            connection = self._accept_connection(addr, data)
                        except RuntimeError:
                            # Initial 以外の未知パケットは破棄する
                            continue

                else:
                    connection.receive(data, self._local_addr, addr)
                    # CID ローテーションに追従するため、接触のたびに索引を
                    # 最新化する
                    self._refresh_dcid_index(connection)

                await self._drain_connection_events(addr, connection)
                await self._send_to(addr, connection)

            except TimeoutError:
                pass

            for addr, connection in list(self._connections.items()):
                timeout = connection.get_timeout()
                if timeout is not None and timeout <= 0:
                    connection.handle_timeout()
                    await self._send_to(addr, connection)
                    await self._drain_connection_events(addr, connection)

            await asyncio.sleep(0.001)
