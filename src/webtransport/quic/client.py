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

        # バックグラウンド受信タスク。connect() が起動し、close() までの
        # 受信イベント処理を担う
        self._recv_task: asyncio.Task[None] | None = None
        # connect() のハンドシェイク完了待ちに使う Future
        self._connect_waiter: asyncio.Future[bool] | None = None
        # 接続終了を待機者へ伝える共有経路 (recv_stream_data 等が待つ)
        self._connection_closed_event = asyncio.Event()
        # バックグラウンド受信タスクの異常終了時に保持する元の例外
        self._task_error: BaseException | None = None
        # コールバック実行中フラグ。コールバック内 (サブタスク経由を含む)
        # からの close() がタスク完了待ちでデッドロックしないための再入ガード
        self._in_callback = False
        # connect() 実行中フラグ。実行中の early data 登録を拒否する
        self._connecting = False

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
        if self._recv_task is not None or self._connecting:
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

    def _resolve_connect(self, result: bool) -> None:
        """connect() の待機者へ結果を通知する

        Args:
            result: connect() が返す値
        """
        waiter = self._connect_waiter
        if waiter is not None and not waiter.done():
            waiter.set_result(result)

    def _resolve_connect_error(self, exc: BaseException) -> None:
        """connect() の待機者へタスク異常終了を通知する

        Args:
            exc: タスクが保持した元の例外
        """
        waiter = self._connect_waiter
        if waiter is not None and not waiter.done():
            waiter.set_exception(exc)

    async def _run_callback(self, callback: Callable[..., Awaitable[None]], *args: object) -> None:
        """コールバックを実行中フラグ付きで呼び出す

        コールバックはバックグラウンド受信タスク内で await される。コールバック
        内 (またはコールバックが起動したサブタスク内) から close() を呼んだ場合、
        close() がタスク自身の完了を待つとデッドロックするため、実行中フラグを
        立てて close() の再入ガードとして使う。

        Args:
            callback: 呼び出すコールバック
            *args: コールバックへ渡す引数
        """
        self._in_callback = True
        try:
            await callback(*args)
        finally:
            self._in_callback = False

    async def _handle_received_events(self) -> None:
        """受信イベントを取り込んで処理する

        STREAM_DATA / DATAGRAM はコールバックを発火し、HANDSHAKE_COMPLETED /
        CONNECTION_CLOSED は接続状態を更新して connect() の待機者を起床する。
        コールバック内で close() が呼ばれた場合は続きのイベントを処理せず
        ループを抜ける (クローズ済み socket への送信を避ける)。
        """
        if self._connection is None:
            return

        while True:
            event = self._connection.next_event()
            if event is None:
                break

            if event.type == quic_low.EventType.HANDSHAKE_COMPLETED:
                self._connected = True
                if self._on_handshake_completed is not None:
                    await self._run_callback(self._on_handshake_completed)
                self._resolve_connect(True)

            elif event.type == quic_low.EventType.STREAM_DATA:
                if self._on_stream_data is not None:
                    await self._run_callback(
                        self._on_stream_data,
                        event.stream_id,
                        event.data,
                        event.fin,
                    )

            elif event.type == quic_low.EventType.DATAGRAM:
                if self._on_datagram is not None:
                    await self._run_callback(self._on_datagram, event.data)

            elif event.type == quic_low.EventType.SESSION_TICKET:
                await self._handle_session_ticket_event(event)

            elif event.type == quic_low.EventType.EARLY_DATA_REJECTED:
                await self._handle_early_data_rejected_event()

            elif event.type == quic_low.EventType.CONNECTION_CLOSED:
                # 接続終了時は即座に状態を落とし、待機者へ通知する
                self._connected = False
                self._running = False
                self._connection_closed_event.set()
                self._resolve_connect(False)
                if self._on_connection_closed is not None:
                    await self._run_callback(self._on_connection_closed)

            if not self._running:
                break

    async def _background_recv(self) -> None:
        """バックグラウンド受信タスク

        socket の読み取り、イベントの取り込み・処理、ACK / フロー制御 /
        ハンドシェイク継続パケットの送出、タイマー処理を担う。connect() の
        ハンドシェイク完了待ちと受信イベント処理、接続終了の検知と待機者
        への通知を並行に進める。close() までの受信処理を担うため、明示的な
        run() 起動は不要になる。
        """
        # ソケット差し替え (migrate) 由来の一時的な受信 OSError を
        # 再参照で解消する。再参照しても続く恒久的な受信エラーはタスク
        # 異常終了として扱う (busy-loop を避けるため)
        receive_error_count = 0
        try:
            while self._running:
                try:
                    await self._receive()
                except OSError:
                    if not self._running:
                        break
                    receive_error_count += 1
                    if receive_error_count >= 3:
                        raise
                    continue
                receive_error_count = 0

                await self._handle_received_events()

                if not self._running:
                    break

                await self._send_pending()

                if self._connection is not None:
                    timeout = self._connection.get_timeout()
                    if timeout is not None and timeout <= 0:
                        self._connection.handle_timeout()

                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            # 外部からのキャンセルでも connect() の待機者を永久待機させない
            self._resolve_connect(False)
            raise
        except BaseException as exc:
            # タスク異常終了: 元の例外を保持し、connect() の待機者へ伝播する。
            # 接続終了と同じ共有経路で待機者 (recv_stream_data 等) を起床させる。
            # 受信パイプラインが死ぬため状態も落とす
            self._task_error = exc
            self._running = False
            self._connected = False
            self._connection_closed_event.set()
            self._resolve_connect_error(exc)
            raise
        else:
            # 正常終了 (close() による停止)。connect() 待機中なら False を返す。
            # close() 後は新たなデータが来ないため、待機者を起床させる
            self._connection_closed_event.set()
            self._resolve_connect(False)

    async def _handle_session_ticket_event(self, event: quic_low.Event) -> None:
        """SESSION_TICKET イベントを処理する"""
        self._latest_session_ticket = event.data
        if self._on_session_ticket is not None:
            await self._run_callback(self._on_session_ticket, event.data)

    async def _handle_early_data_rejected_event(self) -> None:
        """EARLY_DATA_REJECTED イベントを処理する"""
        if self._on_early_data_rejected is not None:
            await self._run_callback(self._on_early_data_rejected)

    async def connect(self) -> bool:
        """サーバーに接続する

        接続作成と 0-RTT 送出を行い、バックグラウンド受信タスクを起動して
        ハンドシェイク完了を待つ。ハンドシェイク完了後も受信タスクは
        close() まで動作し続けるため、明示的に run() を起動しなくても
        受信イベントが処理される。

        Returns:
            接続に成功した場合は True
        """
        if self._recv_task is not None or self._connecting:
            raise RuntimeError("connect() has already been called")

        # connect() 実行中は early data 登録を拒否する (実行中に登録されても
        # _flush_early_data() は走り終えているため黙って破棄される)
        self._connecting = True

        try:
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

            # バックグラウンド受信タスクを起動し、ハンドシェイク完了を待つ
            self._connect_waiter = asyncio.get_running_loop().create_future()
            self._recv_task = asyncio.create_task(self._background_recv())

            return await self._connect_waiter
        except asyncio.CancelledError:
            # 接続待機がキャンセルされた場合、タスク側の解決を無効化する。
            # 受信タスクが未作成ならソケットをクローズし、作成済みなら
            # close() が後始末する
            if self._connect_waiter is not None:
                self._connect_waiter.cancel()
            if self._recv_task is None and self._socket is not None:
                self._socket.close()
                self._socket = None
            raise
        except BaseException:
            # 接続確立前に失敗した場合はソケットをクローズして FD リークを防ぐ
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            raise
        finally:
            self._connecting = False

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

        バックグラウンド受信タスクの完了 (接続終了) まで待つ。受信処理は
        バックグラウンドタスクが担うため、このメソッドは接続終了待ちで
        ある。キャンセルされた場合はバックグラウンドタスクへ伝播しない
        (close() まで受信処理を継続する)。
        """
        if self._recv_task is None:
            raise RuntimeError("client is not connected")

        # shield() により run() のキャンセルはバックグラウンドタスクへ伝播せず、
        # 受信タスクは close() まで継続する
        await asyncio.shield(self._recv_task)

    async def close(self) -> None:
        """接続を閉じる"""
        self._running = False
        self._connected = False

        # バックグラウンド受信タスクを終了フラグで停止させ、完了を待つ。
        # close() 中にタスクが異常終了しても把握するため例外は握る。
        # コールバック内 (サブタスク経由を含む) からの close() はタスクが
        # 自分自身の完了を待つとデッドロックするため、完了待ちをスキップする
        task = self._recv_task
        if task is not None:
            if task.done():
                # タスクが既に終了している場合 (異常終了含む) も例外を回収して
                # "Task exception was never retrieved" 警告を抑える
                if not task.cancelled():
                    task.exception()
            elif not self._in_callback and asyncio.current_task() is not task:
                await asyncio.gather(task, return_exceptions=True)

        try:
            if self._connection is not None:
                self._connection.close()
                await self._send_pending()
        finally:
            # _send_pending() が OSError を raise しても socket クローズは
            # 必ず実行する (FD リークを防ぐ)
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
