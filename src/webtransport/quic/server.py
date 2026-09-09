"""QUIC サーバー

asyncio と UDP を使用した高レベル QUIC サーバー実装。
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import TYPE_CHECKING, Self

from webtransport.webtransport_ext import quic as quic_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


def _validate_cert_key_files(certfile: str | None, keyfile: str | None) -> None:
    """証明書と鍵ファイルの存在と読み取り可能性を検証する

    None は未設定として検証を素通りする (接続時に既定動作になる)。
    起動後の削除・権限変更は run() 時の再 raise で検出する。

    Args:
        certfile: 証明書ファイルパス
        keyfile: 秘密鍵ファイルパス

    Raises:
        FileNotFoundError: ファイルが存在しない場合
        PermissionError: ファイルが読み取り不可の場合
    """
    for name, path in (("certfile", certfile), ("keyfile", keyfile)):
        if path is None:
            continue
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{name} not found: {path}")
        if not os.access(path, os.R_OK):
            raise PermissionError(f"{name} is not readable: {path}")


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
        # 接続ごとのアプリコールバック配送キューと実行タスク。
        # 受信ループは drain したイベントを投入するのみで await しない。
        # 同一接続内の順序保証のため接続ごとに単独タスクとする
        self._connection_queues: dict[
            quic_low.Connection, asyncio.Queue[tuple[tuple[str, int], quic_low.Event]]
        ] = {}
        self._connection_tasks: dict[quic_low.Connection, asyncio.Task[None]] = {}
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
        _validate_cert_key_files(self._certfile, self._keyfile)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind((self._host, self._port))
        self._local_addr = self._normalize_addr(self._socket.getsockname())
        self._actual_port = self._local_addr[1]
        self._running = True

    async def stop(self) -> None:
        """サーバーを停止する"""
        self._running = False
        # 接続タスクを先に止める (受信ループと並行する
        # アプリコールバックが残らないようにする)。コールバック内から
        # stop() された場合は自身を待たない (自己 await を避ける)
        current_task = asyncio.current_task()
        targets = [task for task in self._connection_tasks.values() if task is not current_task]
        for task in targets:
            task.cancel()
        # gather で例外を値として回収する (blind except を避ける)
        results = await asyncio.gather(*targets, return_exceptions=True)
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, BaseException):
                logger.warning("connection task ended with error: %s", result)
        self._connection_tasks.clear()
        self._connection_queues.clear()
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
        if not self._running:
            raise RuntimeError("server is stopped")

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

    def _discard_connection(self, connection: quic_low.Connection) -> None:
        """終了済み接続の登録を全て外す (受信ループ側のみが呼ぶ)

        ローカル close 済みで CONNECTION_CLOSED イベントが来ない接続用。
        CLOSED 排水時は `_enqueue_connection_events` 内で既に外れるため、
        ここには残らない
        """
        self._remove_connection(connection)
        task = self._connection_tasks.pop(connection, None)
        if task is not None and not task.done():
            task.cancel()
        self._connection_queues.pop(connection, None)

    def _ensure_connection_task(self, connection: quic_low.Connection) -> None:
        """接続の配送タスクがなければ生成する"""
        if not self._running:
            return
        task = self._connection_tasks.get(connection)
        if task is not None and not task.done():
            return
        # 残存キューがあれば再利用する (異常終了時の未処理分を失わない)
        queue = self._connection_queues.get(connection)
        if queue is None:
            queue = asyncio.Queue()
            self._connection_queues[connection] = queue
        task = asyncio.create_task(self._run_connection_loop(connection, queue))
        task.add_done_callback(
            lambda done_task, conn=connection: self._on_connection_task_done(conn, done_task)
        )
        self._connection_tasks[connection] = task

    def _on_connection_task_done(
        self, connection: quic_low.Connection, task: asyncio.Task[None]
    ) -> None:
        """接続タスク終了時の後処理。例外時は記録後に当該接続を閉じる"""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        logger.exception("connection task failed, closing connection")
        try:
            connection.close()
        except (OSError, RuntimeError) as close_exc:
            logger.warning("failed to close connection: %s", close_exc)

    def _enqueue_connection_events(
        self, addr: tuple[str, int], connection: quic_low.Connection
    ) -> None:
        """受信後のイベントをキューへ投入する。待たずに戻る

        接続マッピングの削除は本関数 (受信ループ側) のみが行い、
        キューとタスクの削除は `_run_connection_loop` の `finally` が行う。
        アプリコールバックの実行は接続タスクに委ねる。
        キューは無制限であり背圧をかけない (低速接続の洪水で膨らみ得る。
        上限と溢れ方針は別途定める)
        """
        self._ensure_connection_task(connection)
        queue = self._connection_queues.get(connection)
        if queue is None:
            return
        while True:
            event = connection.next_event()
            if event is None:
                break
            queue.put_nowait((addr, event))
            if event.type == quic_low.EventType.CONNECTION_CLOSED:
                self._remove_connection(connection)

    async def _dispatch_connection_event(
        self, addr: tuple[str, int], event: quic_low.Event
    ) -> bool:
        """1 イベントを実行する。終了イベントなら True を返す"""
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
            return True
        return False

    async def _run_connection_loop(
        self,
        connection: quic_low.Connection,
        queue: asyncio.Queue[tuple[tuple[str, int], quic_low.Event]],
    ) -> None:
        """接続ごとのアプリコールバック実行ループ

        キューから取り出して順序どおりに実行する。コールバック例外時は
        タスクを異常終了させ、終了コールバックが記録と接続 close を行う。
        close() 後の送出と CONNECTION_CLOSED 通知は受信ループの
        周期的走査に委ねる。終了時は自身の登録を外す (未処理分のある
        キューは次タスクのために残す)
        """
        try:
            while True:
                queued_addr, event = await queue.get()
                # 配送直前に現アドレスを解決する (Migration で張り替わるため。
                # 削除済みは投入時アドレスへ退行する)
                addr = self._conn_addr.get(connection, queued_addr)
                finished = await self._dispatch_connection_event(addr, event)
                if finished:
                    break
                # コールバック内から stop() された場合は自身も抜ける
                # (登録は stop() 側で消えているため finally は無害)
                if not self._running:
                    break
        finally:
            # 未処理分のあるキューは次タスクのために残す
            if queue.empty():
                if self._connection_queues.get(connection) is queue:
                    del self._connection_queues[connection]
                current = self._connection_tasks.get(connection)
                if current is asyncio.current_task():
                    del self._connection_tasks[connection]

    async def _send_to(self, addr: tuple[str, int], connection: quic_low.Connection) -> None:
        """接続の送信待ちパケットを 1 つ送出する

        パケットにリモートアドレスが埋まっていればそれを使い、
        未設定ならマップ上のクライアントアドレスにフォールバックする。
        send() の連続 drain は ACK 待ちが必要なケースでハングするため行わない。
        削除済み接続への送信は何もしない (終了済み接続の残存送信物は送らない)
        """
        if self._socket is None:
            return
        if connection not in self._conn_addr:
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
                                # 終了時はイベント投入と登録外しのために
                                # 投入する。通知先は登録済みの旧アドレスに
                                # する。破棄時は何もしない (滞留イベントの
                                # 誤帰属を防ぐ)
                                self._enqueue_connection_events(
                                    self._conn_addr.get(candidate, addr), candidate
                                )
                    if connection is None:
                        # 停止競合時は新規受理せずループを抜ける (停止由来の
                        # RuntimeError をパケット破棄ログに混ぜないため)
                        if not self._running:
                            break
                        try:
                            connection = self._accept_connection(addr, data)
                        except ValueError as exc:
                            # 設定不正は黙殺せず再 raise して run() を止める
                            logger.warning(
                                "failed to accept QUIC connection from %s:%d "
                                "size %d first_byte %s: %s",
                                addr[0],
                                addr[1],
                                len(data),
                                data[:1].hex() if data else "none",
                                exc,
                            )
                            raise
                        except RuntimeError as exc:
                            # パケット不正のみ破棄して継続する
                            logger.warning(
                                "discarding invalid QUIC packet from %s:%d "
                                "size %d first_byte %s: %s",
                                addr[0],
                                addr[1],
                                len(data),
                                data[:1].hex() if data else "none",
                                exc,
                            )
                            continue

                else:
                    connection.receive(data, self._local_addr, addr)
                    # CID ローテーションに追従するため、接触のたびに索引を
                    # 最新化する
                    self._refresh_dcid_index(connection)

                # アプリコールバックを待たず投入のみ行い、送信は全接続走査に委ねる
                self._enqueue_connection_events(addr, connection)

            except TimeoutError:
                pass

            # 受信の有無にかかわらず全接続の送信とタイマーを処理する。
            # 1 接続のコールバック実行中も他接続の ACK と再送が止まらない
            # よう、受信ループ側は await するコールバックを持たない。
            # 1 接続の失敗で他接続が止まらないよう接続ごとに例外を隔離する
            # (低レベルが想定外の例外を送出しても run 全体を落とさない)
            for connection in list(self._conn_addr.keys()):
                try:
                    current_addr = self._conn_addr.get(connection)
                    if current_addr is None:
                        continue
                    timeout = connection.get_timeout()
                    if timeout is not None and timeout <= 0:
                        connection.handle_timeout()
                    await self._send_to(current_addr, connection)
                    current_addr = self._conn_addr.get(connection)
                    if current_addr is None:
                        continue
                    self._enqueue_connection_events(current_addr, connection)
                    # ローカル close 済みで CLOSED が来ない接続を回収する
                    if connection.is_closed() and connection in self._conn_addr:
                        self._discard_connection(connection)
                except (OSError, RuntimeError) as exc:
                    logger.warning("failed to send packet: %s", exc)

            await asyncio.sleep(0.001)
