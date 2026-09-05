"""QUIC クライアント

asyncio と UDP を使用した高レベル QUIC クライアント実装。
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import socket
from typing import TYPE_CHECKING, Self

from webtransport.webtransport_ext import quic as quic_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# コールバック実行中かどうかを表すコンテキスト変数。コールバックが起動した
# サブタスクにも伝播するため、コールバック内 (サブタスク経由を含む) からの
# 再入呼び出しを検出できる。コールバックとは無関係の別タスクには伝播しない
# ため、誤検出しない
_in_callback_var: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "webtransport_quic_in_callback",
    default=False,
)


class _StreamRecvState:
    """ストリームごとの受信状態

    recv_stream_data が FIN まで受信するための累積連結と完了判定、および
    wait_for_stream_reset が待つ STREAM_RESET のエラーコードを管理する。
    受信データの追加・FIN 受信・STREAM_RESET 受信のたびに event を set し、
    待機者 (recv_stream_data / wait_for_stream_reset) を起床する。接続終了
    とタスク異常終了と close() のときは _wake_stream_waiters が全状態の event
    を set する。
    """

    def __init__(self) -> None:
        # 受信データの累積連結
        self.data = bytearray()
        # FIN を受信したかどうか
        self.fin = False
        # STREAM_RESET 受信時のアプリケーションエラーコード (未受信なら None)
        self.reset_error_code: int | None = None
        # 状態更新を待機者へ通知するイベント
        self.event = asyncio.Event()


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
        max_datagram_frame_size: int | None = None,
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
            max_datagram_frame_size: DATAGRAM の受信サポート広告に使う
                最大フレームサイズ (RFC 9221 Section 3。将来改訂される可能性が
                ある)。None (既定) なら低レベル Config の既定値
                (enable_datagram=true / max_datagram_frame_size=65536) を使う。
                0 なら DATAGRAM を広告せず、ローカルの send_datagram() も
                無効化される。範囲外の値は connect() で ValueError
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
        self._max_datagram_frame_size = max_datagram_frame_size

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
        # connect() 実行中フラグ。実行中の early data 登録を拒否する
        self._connecting = False
        # ストリームごとの受信状態 (recv_stream_data 用)
        self._recv_states: dict[int, _StreamRecvState] = {}

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

    async def _send_pending(self) -> int:
        """送信待ちパケットを 1 つ送出する

        send() を ACK なしで連続 drain すると、ストリームデータ滞留時に
        戻ってこなくなるため、1 呼び出しあたり 1 パケットに留める。

        Returns:
            送信したパケット数 (0 または 1。送信しない場合は 0)
        """
        if self._connection is None or self._socket is None:
            return 0

        packet = self._connection.send()
        if packet is None:
            return 0

        loop = asyncio.get_running_loop()
        await loop.sock_sendto(
            self._socket,
            packet.data,
            self._destination_for_packet(packet),
        )
        return 1

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
        立てて close() の再入ガードとして使う。フラグはコンテキスト変数で管理し、
        コールバックが await するサブタスクにも伝播させる。

        Args:
            callback: 呼び出すコールバック
            *args: コールバックへ渡す引数
        """
        token = _in_callback_var.set(True)
        try:
            await callback(*args)
        finally:
            _in_callback_var.reset(token)

    def _discard_recv_state(self, stream_id: int) -> None:
        """ストリーム受信状態のエントリを破棄する

        破棄前に待機者を起床させ、閉じ込めを防ぐ。エントリが無ければ
        何もしない。破棄後に同一ストリームへ再受信・再呼び出しがあった
        場合は `setdefault` で新しい空状態が作られ、通常の待機に入る。

        Args:
            stream_id: 破棄するストリーム ID
        """
        state = self._recv_states.get(stream_id)
        if state is None:
            return
        state.event.set()
        del self._recv_states[stream_id]

    def discard_recv_state(self, stream_id: int) -> None:
        """ストリーム受信状態を明示的に破棄する

        `recv_stream_data` を呼ばないコールバック専用の利用者が、自分の
        都合で受信状態を解放するために使う。当該 `stream_id` のエントリ
        があれば削除し、存在しなければ何もしない。破棄前に待機者を起床
        させるため、`wait_for_stream_reset` の待機者は閉じ込められない。
        起床した待機者は新しい空状態から待機を継続する。

        破棄した `stream_id` に対する `recv_stream_data` /
        `wait_for_stream_reset` の再呼び出しは通常の待機ループに入り、
        進捗が無ければ `overall_timeout` / `timeout` で `TimeoutError`
        になる。破棄済みストリームへの再呼び出しは避けること。
        `wait_for_stream_reset` でエラーコードを取得した後は、受信状態が
        残るため本メソッドでの解放を推奨する。本メソッドは同期のため、
        コールバック内 (`on_stream_data` 等) から呼び出せる。

        Args:
            stream_id: 破棄するストリーム ID
        """
        self._discard_recv_state(stream_id)

    def _update_recv_state(self, stream_id: int, data: bytes, fin: bool) -> None:
        """ストリーム受信状態を更新する

        STREAM_DATA イベントのデータを累積連結し、FIN を受信したら完了を
        立てて待機者を起床する。ngtcp2 はデータを offset の非減少順・重複
        なしで連続配送する保証があるため、reorder 再構成 (gap 検出 / 重複
        セグメントのマージ / final size の整合性検証) は行わない。

        Args:
            stream_id: ストリーム ID
            data: 受信データ
            fin: FIN フラグ
        """
        state = self._recv_states.setdefault(stream_id, _StreamRecvState())
        state.data.extend(data)
        if fin:
            state.fin = True
        state.event.set()

    def _handle_stream_reset(self, stream_id: int, error_code: int) -> None:
        """STREAM_RESET イベントを処理する

        リセットのアプリケーションエラーコードを受信状態に記録し、待機者
        へ通知する。recv_stream_data は進捗として idle deadline を 1 回延長し、
        wait_for_stream_reset はエラーコードを参照する。

        Args:
            stream_id: ストリーム ID
            error_code: ピアが送ったアプリケーションエラーコード
        """
        state = self._recv_states.setdefault(stream_id, _StreamRecvState())
        state.reset_error_code = error_code
        state.event.set()

    def _wake_stream_waiters(self) -> None:
        """全ストリームの待機者を起床する

        接続終了・タスク異常終了・close() 時に呼び出し、受信待機中の
        recv_stream_data へ通知する。待機側は _connection_closed_event と
        _task_error を確認し、接続終了なら TimeoutError、タスク異常終了なら
        元の例外を raise する。
        """
        for state in self._recv_states.values():
            state.event.set()

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
                # ストリーム受信状態を更新してからコールバックを発火する
                self._update_recv_state(event.stream_id, event.data, event.fin)
                if self._on_stream_data is not None:
                    await self._run_callback(
                        self._on_stream_data,
                        event.stream_id,
                        event.data,
                        event.fin,
                    )

            elif event.type == quic_low.EventType.STREAM_RESET:
                self._handle_stream_reset(event.stream_id, event.error_code)

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
                self._wake_stream_waiters()
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
            # 外部からのキャンセルでも connect() の待機者と受信待機者を
            # 永久待機させない
            self._running = False
            self._connected = False
            self._connection_closed_event.set()
            self._wake_stream_waiters()
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
            self._wake_stream_waiters()
            self._resolve_connect_error(exc)
            raise
        else:
            # 正常終了 (close() による停止)。connect() 待機中なら False を返す。
            # close() 後は新たなデータが来ないため、待機者を起床させる
            self._connection_closed_event.set()
            self._wake_stream_waiters()
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

    async def connect(self, timeout: float = 10.0) -> bool:
        """サーバーに接続する

        接続作成と 0-RTT 送出を行い、バックグラウンド受信タスクを起動して
        ハンドシェイク完了を待つ。ハンドシェイク完了後も受信タスクは
        close() まで動作し続けるため、明示的に run() を起動しなくても
        受信イベントが処理される。

        timeout はハンドシェイク完了までの全体タイムアウトである。期限までに
        確立できない場合は接続を維持したまま False を返す (ハンドシェイクが
        後で完了する可能性がある。後始末は close() が担う)。timeout <= 0 の
        ときは接続を開始せずに即座に False を返す。

        Args:
            timeout: ハンドシェイク完了までのタイムアウト (秒)

        Returns:
            接続に成功した場合は True。期限までに確立できない場合・接続
            失敗時は False
        """
        if self._recv_task is not None or self._connecting:
            raise RuntimeError("connect() has already been called")

        # timeout <= 0 のときは接続を開始せずに即座に False を返す
        # (ngtcp2-py と同じ挙動)
        if timeout <= 0:
            return False

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
            if self._max_datagram_frame_size is not None:
                # RFC 9221 Section 3 の通り、max_datagram_frame_size は受信
                # サポートの広告であり、DATAGRAM の送信はピアの非ゼロ広告に
                # 依存する (将来改訂される可能性がある)。値は変長整数
                # (2^62 - 1 が上限) で表現される (RFC 9000 Section 16)
                if not 0 <= self._max_datagram_frame_size < 2**62:
                    raise ValueError(f"max_datagram_frame_size must be in range [0, {2**62 - 1}]")
                if self._max_datagram_frame_size == 0:
                    config.enable_datagram = False
                else:
                    config.enable_datagram = True
                    config.max_datagram_frame_size = self._max_datagram_frame_size

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

            try:
                return await asyncio.wait_for(self._connect_waiter, timeout=timeout)
            except TimeoutError:
                # バックグラウンドタスクの異常終了 (元の例外が TimeoutError の
                # 場合) をタイムアウトと区別する。タスク異常終了時は元の例外を
                # 伝播し、真のタイムアウトのみ False を返す
                if self._task_error is not None:
                    raise self._task_error from None
                return False
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

    def _recv_stream_timeout_error(
        self,
        reason: str,
        stream_id: int,
        timeout: float,
        overall_timeout: float,
        received: int,
    ) -> TimeoutError:
        """recv_stream_data のタイムアウトエラーを組み立てる

        Args:
            reason: タイムアウトの理由 (connection closed / overall timeout /
                idle timeout)
            stream_id: ストリーム ID
            timeout: idle タイムアウト (秒)
            overall_timeout: 全体タイムアウト (秒)
            received: 受信済みバイト数

        Returns:
            組み立てた TimeoutError
        """
        return TimeoutError(
            f"{reason} while waiting for stream data "
            f"(stream_id={stream_id}, timeout={timeout}, "
            f"overall_timeout={overall_timeout}, received={received} bytes)"
        )

    async def recv_stream_data(
        self,
        stream_id: int,
        timeout: float = 10.0,
        *,
        overall_timeout: float | None = None,
    ) -> tuple[bytes, bool]:
        """ストリームデータを FIN まで受信する

        STREAM_DATA イベントを累積連結し、FIN を受信したら (受信データ, fin)
        を返す。呼び出し時点で既に FIN 完了済みのストリームは即時 return する。
        バックグラウンド受信タスクが受信イベントを処理するため、run() を明示
        起動しなくても動作する。

        タイムアウトは 2 段構え:
        - timeout (idle deadline): 進捗 (待機中のストリームの STREAM_DATA
          受信) があるたびに延びる。進捗が無いまま timeout 秒経過で
          TimeoutError
        - overall_timeout (absolute deadline): 進捗に関係なく動かない。
          None なら max(timeout * 6, 30) を使う

        FIN と期限の検出が同時になった場合は FIN を優先する。ゼロ長 FIN
        (datalen=0, fin=True) も完了として扱う。接続終了 (CONNECTION_CLOSED)
        を受信した場合も待機を終了し TimeoutError を raise する。コールバック
        内から呼び出すと受信処理が進まないため RuntimeError を raise する。

        FIN で正常 return したストリームの受信状態は自動破棄される
        (以後のデータ到着は期待しない使い方のため)。破棄した `stream_id`
        への再呼び出しは通常の待機ループに入り、進捗が無ければ
        `overall_timeout` で `TimeoutError` になるため避けること。同一
        ストリームへの `recv_stream_data` と `wait_for_stream_reset` の
        並行呼び出しは、正常 return 時点で `wait_for_stream_reset` 側が
        新規待機に切り替わる。

        Args:
            stream_id: ストリーム ID
            timeout: idle タイムアウト (秒)
            overall_timeout: 全体タイムアウト (秒)。None なら
                max(timeout * 6, 30)

        Returns:
            (受信データ, fin)。正常 return では fin は常に True

        Raises:
            TimeoutError: idle deadline / overall_timeout に達した場合、
                または接続終了時
            RuntimeError: コールバック内から呼び出した場合、または未接続時
            ValueError: timeout / overall_timeout が 0 以下の場合
            バックグラウンド受信タスクの異常終了時は、その元の例外を re-raise
            する
        """
        if self._recv_task is not None and _in_callback_var.get():
            raise RuntimeError("recv_stream_data() cannot be called from within a callback")
        if self._recv_task is None:
            raise RuntimeError("client is not connected")

        if overall_timeout is None:
            overall_timeout = max(timeout * 6, 30)

        if timeout <= 0 or overall_timeout <= 0:
            raise ValueError("timeout and overall_timeout must be positive")

        state = self._recv_states.setdefault(stream_id, _StreamRecvState())

        if state.fin:
            # 既に FIN 完了済みの即時 return でも自動破棄する
            result = bytes(state.data)
            self._discard_recv_state(stream_id)
            return result, True

        loop = asyncio.get_running_loop()
        overall_deadline = loop.time() + overall_timeout

        while True:
            if state.fin:
                result = bytes(state.data)
                self._discard_recv_state(stream_id)
                return result, True
            if self._task_error is not None:
                raise self._task_error
            if self._connection_closed_event.is_set():
                raise self._recv_stream_timeout_error(
                    "connection closed",
                    stream_id,
                    timeout,
                    overall_timeout,
                    len(state.data),
                )
            current = self._recv_states.get(stream_id)
            if current is not state:
                # 自動破棄・明示破棄でエントリが除去された。新しい空状態
                # から待機を継続する (破棄前の event.set() で起床済み)
                state = self._recv_states.setdefault(stream_id, _StreamRecvState())
                continue

            if loop.time() >= overall_deadline:
                raise self._recv_stream_timeout_error(
                    "overall timeout",
                    stream_id,
                    timeout,
                    overall_timeout,
                    len(state.data),
                )

            # 待機前に永続状態を記録する。タイムアウトと同時刻に進捗が届いた
            # 場合の判定に使う (event は他待機者 (wait_for_stream_reset) が
            # clear する可能性があるため、永続状態の変化で進捗を検出する)
            received_before = len(state.data)
            reset_before = state.reset_error_code

            try:
                # idle deadline は進捗があるたびに延びるため、毎回 timeout 秒で
                # 待つ。absolute deadline (overall_timeout) が先に来る場合は
                # そちらで待機を打ち切る。STREAM_RESET も進捗として扱う
                remaining_overall = overall_deadline - loop.time()
                wait_timeout = min(timeout, remaining_overall)
                await asyncio.wait_for(state.event.wait(), timeout=wait_timeout)
            except TimeoutError:
                # 進捗が無いまま idle deadline に達した。同時に FIN が
                # 届いていた場合は FIN を優先する
                if state.fin:
                    result = bytes(state.data)
                    self._discard_recv_state(stream_id)
                    return result, True
                if self._task_error is not None:
                    raise self._task_error from None
                if self._connection_closed_event.is_set():
                    raise self._recv_stream_timeout_error(
                        "connection closed",
                        stream_id,
                        timeout,
                        overall_timeout,
                        len(state.data),
                    ) from None
                if loop.time() >= overall_deadline:
                    raise self._recv_stream_timeout_error(
                        "overall timeout",
                        stream_id,
                        timeout,
                        overall_timeout,
                        len(state.data),
                    ) from None
                # タイムアウトと同時刻に進捗 (非 FIN データ / STREAM_RESET) が
                # 届いていた場合は idle timeout とせず待機を継続する
                if len(state.data) > received_before or state.reset_error_code != reset_before:
                    continue
                raise self._recv_stream_timeout_error(
                    "idle timeout",
                    stream_id,
                    timeout,
                    overall_timeout,
                    len(state.data),
                ) from None

            state.event.clear()

    async def shutdown_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームを中断する

        低レベル `Connection.close_stream` を呼び、RESET_STREAM
        (RFC 9000 Section 19.4) と STOP_SENDING (Section 19.5) をスケジュール
        して送出する。フレームの実際の送出は `_send_pending()` が担う
        (既存の `send_stream_data` と同じパターン)。双方向ストリームでは
        両方を送出する。単方向ストリームでは `ngtcp2_conn_shutdown_stream`
        がローカル単方向なら write 側 (RESET_STREAM) のみ、リモート単方向
        なら read 側 (STOP_SENDING) のみを shutdown する。

        RESET_STREAM の送出は状態依存である。書き込み側が全データ送信済み +
        FIN 確認済みの場合は RESET_STREAM を送出しない (書き込み側が既に
        完了しているため)。

        Args:
            stream_id: ストリーム ID
            error_code: アプリケーションエラーコード
        """
        if self._connection is None:
            return

        self._connection.close_stream(stream_id, error_code)
        await self._send_pending()

    async def wait_for_stream_reset(
        self,
        stream_id: int,
        timeout: float = 10.0,
    ) -> int:
        """ピアの RESET_STREAM 受信を待ち、そのアプリケーションエラーコードを返す

        STREAM_RESET イベントの処理とストリームごとのエラーコード保持は
        バックグラウンド受信タスクが担い、このメソッドはその状態を待つ。
        呼び出し時点で既に RESET_STREAM を受信済みのストリームは即時 return
        する。待機中に接続終了 (CONNECTION_CLOSED) を受信した場合も
        TimeoutError を raise して待機を終了する。コールバック内から呼び出すと
        受信処理が進まないため RuntimeError を raise する。

        受信状態が自動破棄・明示破棄された場合は新しい空状態から待機を
        継続する。破棄した `stream_id` への再呼び出しは通常の待機ループに
        入り、RESET が届かなければ `timeout` で `TimeoutError` になるため
        避けること。破棄前に RESET が届いていても、破棄後に起動した待機は
        旧エラーコードを取得できない。両方が必要な場合は本メソッドを先に
        起動して並行待機すること。

        ngtcp2 は STOP_SENDING を受信すると、ストリームが Ready / Send 状態
        の場合は自動で RESET_STREAM を送出する (RFC 9000 Section 3.5 の
        MUST。エラーコードは STOP_SENDING から複製する SHOULD)。Data Sent
        状態では MAY であり、送出のタイミングは ngtcp2 実装に依存する。
        そのため、`shutdown_stream` を呼んだ後は通常すぐにエラーコードを
        受け取る。

        Args:
            stream_id: ストリーム ID
            timeout: 待機のタイムアウト (秒)

        Returns:
            ピアの RESET_STREAM が運んだアプリケーションエラーコード

        Raises:
            TimeoutError: 期限までに RESET_STREAM を受信しない場合、
                または接続終了時
            RuntimeError: コールバック内から呼び出した場合、または未接続時
            ValueError: timeout が 0 以下の場合
        """
        if self._recv_task is not None and _in_callback_var.get():
            raise RuntimeError("wait_for_stream_reset() cannot be called from within a callback")
        if self._recv_task is None:
            raise RuntimeError("client is not connected")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        state = self._recv_states.setdefault(stream_id, _StreamRecvState())

        if state.reset_error_code is not None:
            return state.reset_error_code

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        while True:
            if state.reset_error_code is not None:
                return state.reset_error_code
            if self._task_error is not None:
                raise self._task_error
            if self._connection_closed_event.is_set():
                raise TimeoutError(
                    f"connection closed while waiting for stream reset "
                    f"(stream_id={stream_id}, timeout={timeout})"
                )
            current = self._recv_states.get(stream_id)
            if current is not state:
                # 自動破棄・明示破棄でエントリが除去された。新しい空状態
                # から待機を継続する (破棄前の event.set() で起床済み)
                state = self._recv_states.setdefault(stream_id, _StreamRecvState())
                continue
            if loop.time() >= deadline:
                raise TimeoutError(
                    f"timeout while waiting for stream reset "
                    f"(stream_id={stream_id}, timeout={timeout})"
                )

            try:
                await asyncio.wait_for(
                    state.event.wait(),
                    timeout=deadline - loop.time(),
                )
            except TimeoutError:
                continue
            state.event.clear()

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

    async def close(self) -> int:
        """接続を閉じる

        Returns:
            close() 中に送出できたパケット数。CONNECTION_CLOSE の送出に
            成功した場合は 1、送出しなかった場合 (未接続時や送信失敗時) は 0
        """
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
            elif not _in_callback_var.get() and asyncio.current_task() is not task:
                await asyncio.gather(task, return_exceptions=True)

        sent = 0
        try:
            if self._connection is not None:
                self._connection.close()
                try:
                    sent += await self._send_pending()
                except OSError as exc:
                    # CONNECTION_CLOSE の送出に失敗した場合は送出できなかった
                    # ものとして 0 を返す
                    logger.warning("failed to send connection close: %s", exc)
        finally:
            # 予期しない例外が発生しても socket クローズは必ず実行する
            # (FD リークを防ぐ)。_send_pending() の OSError は内側の
            # try で捕捉済みのため、ここに漏れてくるのは _connection.close()
            # 由来の予期しない例外のみである
            if self._socket is not None:
                self._socket.close()
                self._socket = None

        return sent

    async def __aenter__(self) -> Self:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.close()
