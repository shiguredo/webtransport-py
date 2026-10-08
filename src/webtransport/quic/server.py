"""QUIC サーバー

asyncio と UDP を使用した高レベル QUIC サーバー実装。
"""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import TYPE_CHECKING, Any, Self

from webtransport._common import (
    normalize_addr,
    recv_datagram,
    validate_cert_key_files,
)
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
        # 接続ごとのアプリコールバック配送キューと実行タスク。
        # 受信ループは drain したイベントを投入するのみで await しない。
        # 同一接続内の順序保証のため接続ごとに単独タスクとする
        self._connection_queues: dict[
            quic_low.Connection, asyncio.Queue[tuple[tuple[str, int], quic_low.Event]]
        ] = {}
        self._connection_tasks: dict[quic_low.Connection, asyncio.Task[None]] = {}
        # アプリコールバックを実行中の接続。ローカル close 後の回収
        # (`_discard_connection`) で接続タスクを cancel すると実行中の
        # コールバックへ CancelledError が注入されるため、実行中は回収を
        # 次の走査へ見送る判断に使う
        self._callbacks_running: set[quic_low.Connection] = set()
        self._running = False
        self._actual_port = 0
        # 受信待ちの上限 (秒)。接続の QUIC タイマー期限に合わせて更新する
        self._wait = 0.1

        self._on_handshake_completed: Callable[[tuple[str, int]], Awaitable[None]] | None = None
        self._on_stream_data: (
            Callable[[int, bytes, bool, tuple[str, int]], Awaitable[None]] | None
        ) = None
        self._on_datagram: Callable[[bytes, tuple[str, int]], Awaitable[None]] | None = None
        self._on_stop_sending: Callable[[int, int, tuple[str, int]], Awaitable[None]] | None = None
        self._on_stream_reset: Callable[[int, int, tuple[str, int]], Awaitable[None]] | None = None
        self._on_connection_closed: Callable[[tuple[str, int]], Awaitable[None]] | None = None

    def on_stop_sending(
        self,
        callback: Callable[[int, int, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """ピアからの STOP_SENDING 受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, error_code: int, addr: tuple[str, int]) -> None
        """
        self._on_stop_sending = callback

    def on_stream_reset(
        self,
        callback: Callable[[int, int, tuple[str, int]], Awaitable[None]],
    ) -> None:
        """ピアからの RESET_STREAM 受信時のコールバックを設定する

        ピアが送信側のストリームを中断したときに、そのアプリケーション
        エラーコードとともに呼ばれる (RFC 9000 Section 19.4。将来改訂される
        可能性がある)。`quic.Client.wait_for_stream_reset` と異なり待機せずに
        通知する。未登録の場合は通知しない。

        Args:
            callback: async def callback(stream_id: int, error_code: int, addr: tuple[str, int]) -> None
        """
        self._on_stream_reset = callback

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

    # 実装は _common.normalize_addr に集約する (self を使わないため staticmethod)
    _normalize_addr = staticmethod(normalize_addr)

    async def start(self) -> None:
        """サーバーを開始する"""
        validate_cert_key_files(self._certfile, self._keyfile)
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
            self._callbacks_running.clear()
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
        ここには残らない。

        アプリコールバックの実行中は回収を次の走査へ見送る。ここで接続
        タスクを cancel すると実行中のコールバックへ CancelledError が
        注入されるためである (`quic.Server.close` は公開 API であり、
        プロトコル違反の通知などコールバックの中からも呼ばれる)。見送る
        場合は登録を外す前に return する。受信ループの回収条件は登録が
        残っていることを前提にするため、外してしまうと次の走査で
        回収されずに残る。コールバックが戻った後の走査で改めて呼ばれる。
        """
        if connection in self._callbacks_running:
            return
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
        elif event.type == quic_low.EventType.STREAM_RESET:
            if self._on_stream_reset is not None:
                await self._on_stream_reset(event.stream_id, event.error_code, addr)
        elif event.type == quic_low.EventType.STOP_SENDING:
            if self._on_stop_sending is not None:
                await self._on_stop_sending(event.stream_id, event.error_code, addr)
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
                # コールバック実行中を記録する (ローカル close 後の回収を
                # 見送る判断に使う)。finally で必ず外す
                self._callbacks_running.add(connection)
                try:
                    finished = await self._dispatch_connection_event(addr, event)
                finally:
                    self._callbacks_running.discard(connection)
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

    async def _send_to(self, addr: tuple[str, int], connection: quic_low.Connection) -> int:
        """接続の送信待ちパケットを送出できるだけ送出する

        パケットにリモートアドレスが埋まっていればそれを使い、
        未設定ならマップ上のクライアントアドレスにフォールバックする。
        send() は輻輳ウィンドウの枯渇・フロー制御・送信待ちの解消のいずれかで
        必ず None を返すため、None まで drain しても戻ってこなくならない。
        削除済み接続への送信は何もしない (終了済み接続の残存送信物は送らない)

        Returns:
            送信したパケット数
        """
        if self._socket is None:
            return 0
        if connection not in self._conn_addr:
            return 0

        loop = asyncio.get_running_loop()
        sent = 0
        while True:
            packet = connection.send()
            if packet is None:
                break

            if packet.remote_host and packet.remote_port:
                dest: tuple[str, int] = (packet.remote_host, packet.remote_port)
            else:
                dest = addr

            await loop.sock_sendto(self._socket, packet.data, dest)
            sent += 1

        # 送信経路の CID 発行に追従するため、送出後に索引を最新化する
        self._refresh_dcid_index(connection)
        return sent

    def initiate_key_update(self, addr: tuple[str, int]) -> bool:
        """指定クライアントの TLS 鍵更新 (RFC 9001 Section 6) を開始する

        ハンドシェイク完了前や `HANDSHAKE_CONFIRMED` 未成立の場合は False を
        返す。鍵更新の確認 (ピアからの応答) 前に連続して呼ぶと 2 回目は
        False になる (RFC 9001 Section 6.1 の MUST)。実際の鍵の切り替えは
        以後に送信するパケットで行われる。

        Args:
            addr: クライアントアドレス

        Returns:
            鍵更新を開始できた場合は True (未登録のアドレスも False)
        """
        connection = self._connections.get(addr)
        if connection is None:
            return False

        return connection.initiate_key_update()

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

        Raises:
            ValueError: addr が登録済みで data が 1 MiB 超の場合
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

        Raises:
            ValueError: addr が登録済みで data が 1 MiB 超の場合
        """
        connection = self._connections.get(addr)
        if connection is None:
            return

        connection.send_datagram(data)
        await self._send_to(addr, connection)

    async def shutdown_stream(
        self,
        addr: tuple[str, int],
        stream_id: int,
        error_code: int = 0,
    ) -> None:
        """ストリームを中断する

        低レベル `Connection.close_stream` を呼び、RESET_STREAM
        (RFC 9000 Section 19.4) と STOP_SENDING (Section 19.5) をスケジュール
        して `_send_to` で送出する。フレームの実際の送出は `_send_to` が担う
        (既存の `send_stream_data` と同じパターン)。RFC 9000 は将来改訂される
        可能性がある。双方向ストリームでは両方を送出する。単方向ストリームでは
        `ngtcp2_conn_shutdown_stream` がローカル単方向なら write 側
        (RESET_STREAM) のみ、リモート単方向なら read 側 (STOP_SENDING) のみを
        shutdown する。

        意味は `quic.Client.shutdown_stream` と同じである。片方だけを送る
        操作は `reset_stream` / `stop_sending` を使う。未登録の `addr` では
        何もしない。

        Args:
            addr: クライアントアドレス
            stream_id: ストリーム ID
            error_code: アプリケーションエラーコード
        """
        connection = self._connections.get(addr)
        if connection is None:
            return

        connection.close_stream(stream_id, error_code)
        await self._send_to(addr, connection)

    async def reset_stream(
        self,
        addr: tuple[str, int],
        stream_id: int,
        error_code: int = 0,
    ) -> None:
        """RESET_STREAM を送出してストリームの送信側を中断する

        低レベル `Connection.reset_stream` を呼び、RESET_STREAM
        (RFC 9000 Section 19.4) をスケジュールして `_send_to` で送出する。
        自身の送信側だけを中断する QUIC フレーム層の操作であり、ピアの送信側
        は止まらない。ピアの送信側も止める場合は `stop_sending` を併用する。
        `h2` 層の同名 API はセッションを閉じ込めた層の操作であるのに対し、
        本層は接続とストリームを指定するフレーム層の操作である。RFC 9000 は
        将来改訂される可能性がある。未登録の `addr` では何もしない。

        Args:
            addr: クライアントアドレス
            stream_id: ストリーム ID
            error_code: アプリケーションエラーコード
        """
        connection = self._connections.get(addr)
        if connection is None:
            return

        connection.reset_stream(stream_id, error_code)
        await self._send_to(addr, connection)

    async def stop_sending(
        self,
        addr: tuple[str, int],
        stream_id: int,
        error_code: int = 0,
    ) -> None:
        """STOP_SENDING を送出してピアの送信側の停止を要求する

        低レベル `Connection.stop_sending` を呼び、STOP_SENDING
        (RFC 9000 Section 19.5) をスケジュールして `_send_to` で送出する。
        ピアの送信側だけを止める QUIC フレーム層の操作であり、自身の送信側は
        止まらない。`h2` 層の同名 API はセッションを閉じ込めた層の操作で
        あるのに対し、本層は接続とストリームを指定するフレーム層の操作で
        ある。RFC 9000 は将来改訂される可能性がある。未登録の `addr` では
        何もしない。

        Args:
            addr: クライアントアドレス
            stream_id: ストリーム ID
            error_code: アプリケーションエラーコード
        """
        connection = self._connections.get(addr)
        if connection is None:
            return

        connection.stop_sending(stream_id, error_code)
        await self._send_to(addr, connection)

    async def close(
        self,
        addr: tuple[str, int],
        error_code: int = 0,
        reason: str = "",
    ) -> None:
        """指定したクライアントとの接続を終了コードと理由付きで閉じる

        低レベル `Connection.close(error_code, reason)` を呼び、
        CONNECTION_CLOSE (RFC 9000 Section 19.19) を `_send_to` で送出する。
        RFC 9000 は将来改訂される可能性がある。ローカル起点の終了として扱い
        `on_connection_closed` は発火しない (`quic.Client.close()` と同じ
        契約)。`stop()` を呼ばずに 1 接続だけを閉じられる。接続の登録解除は
        `run()` の回収経路 (`connection.is_closed()` →
        `_discard_connection`) が行うため、本メソッドの直後はまだ登録が
        残っている。他の接続の通信は継続する。未登録の `addr` では何もしない。
        アプリコールバックの中から呼んでも、実行中のコールバックは
        `CancelledError` で中断されない (回収はコールバックが戻った後の
        走査で行われる)。

        ハンドシェイク完了前の終了は ngtcp2 が終了コードを APPLICATION_ERROR
        に置換して理由を落とす (RFC 9000 Section 10.2.3。将来改訂される
        可能性がある)。終了コードと理由がピアへ伝わるのはハンドシェイク
        完了後である。

        Args:
            addr: クライアントアドレス
            error_code: アプリケーションエラーコード
            reason: 終了理由
        """
        connection = self._connections.get(addr)
        if connection is None:
            return

        connection.close(error_code, reason)
        await self._send_to(addr, connection)

    async def run(self) -> None:
        """メインループを実行する

        サーバーが停止されるまでブロックする。
        """
        if self._socket is None or self._local_addr is None:
            raise RuntimeError("server is not started")

        while self._running:
            # 受信は「次の QUIC タイマー期限まで待つ 1 回」+「読めるだけ
            # まとめて読む」で行う。1 ループ 1 パケットに固定すると、1
            # パケットあたりのループ オーバーヘッドがそのままスループット
            # 上限になる (まとめて受信してから ACK と MAX_STREAM_DATA を
            # 送出することで送出間隔も詰まる)
            #
            # 待機には recv_datagram を使う。asyncio.wait_for で
            # loop.sock_recvfrom を包むと、macOS の kqueue セレクタで
            # タイムアウト時にパケットの読み取り可能通知が失われる
            # (src/webtransport/_common.py の wait_socket_readable 参照)
            result = await recv_datagram(self._socket, self._wait)
            if result is not None:
                data, raw_addr = result
                if not self._handle_datagram(data, raw_addr):
                    break
                # 続きは non-blocking で読めるだけ読む。読み切ったら送信と
                # タイマー処理へ進む
                while True:
                    try:
                        data, raw_addr = self._socket.recvfrom(65535)
                    except BlockingIOError, InterruptedError:
                        break
                    if not self._handle_datagram(data, raw_addr):
                        return

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

            # 受信待ちは次の QUIC タイマー期限に合わせる。固定の sleep を
            # 挟むと受信したパケット数だけ遅延が積み上がるため、待機は
            # ソケット読み取り側に任せ、ここでは他のタスクへ 1 回譲るだけに
            # する (タイマーが期限切れの場合は次の周回で即座に処理される)
            self._wait = self._timeout_seconds()
            await asyncio.sleep(0)

    def _timeout_seconds(self) -> float:
        """全接続のうち最も早い QUIC タイマー期限までの秒数を返す

        タイマーが無い場合は受信待ちの上限 (0.1 秒) を返す。1 回の受信待ちは
        0.001〜0.1 秒に収める。下限を設けないと期限がマイクロ秒単位で来た
        ときに受信待ちがほぼ常にタイムアウトし、送れるようになった直後に
        送り出せない。
        """
        deadline: float | None = None
        for connection in self._conn_addr:
            timeout_ns = connection.get_timeout()
            if timeout_ns is None:
                continue
            seconds = timeout_ns / 1e9
            if deadline is None or seconds < deadline:
                deadline = seconds
        if deadline is None:
            return 0.1
        return min(max(deadline, 0.001), 0.1)

    def _handle_datagram(self, data: bytes, raw_addr: tuple[Any, ...]) -> bool:
        """受信した 1 データグラムを接続へ振り分ける

        新規接続の accept と Connection Migration の照合を行い、既存接続へ
        パケットを渡す。アプリコールバックは待たず、イベントの投入のみ行う。

        Returns:
            受信ループを継続する場合は True、停止する場合は False
        """
        if self._local_addr is None:
            return False

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
                    return False
                try:
                    connection = self._accept_connection(addr, data)
                except ValueError as exc:
                    # 設定不正は黙殺せず再 raise して run() を止める
                    logger.warning(
                        "failed to accept QUIC connection from %s:%d size %d first_byte %s: %s",
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
                        "discarding invalid QUIC packet from %s:%d size %d first_byte %s: %s",
                        addr[0],
                        addr[1],
                        len(data),
                        data[:1].hex() if data else "none",
                        exc,
                    )
                    return True

        else:
            connection.receive(data, self._local_addr, addr)
            # CID ローテーションに追従するため、接触のたびに索引を
            # 最新化する
            self._refresh_dcid_index(connection)

        # アプリコールバックを待たず投入のみ行い、送信は全接続走査に委ねる
        self._enqueue_connection_events(addr, connection)
        return True
