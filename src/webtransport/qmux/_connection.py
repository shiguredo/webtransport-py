"""QMux 接続を asyncio ストリーム上で駆動するドライバ

クライアント (`webtransport.qmux.client`) とサーバー (`webtransport.qmux.server`)
で共有する。Sans-IO の `Connection` に対して読み書き・タイマー・イベント配布を
行い、ストリーム操作をコルーチンとして提供する。

`Connection.pending_record` と `Connection.receive` は同じ接続の内部状態を触る
ため、送信側のコルーチンと受信ループは `asyncio.Lock` で排他する。
"""

import asyncio
from collections.abc import Awaitable, Callable

from webtransport.qmux.exceptions import (
    QmuxConnectionError,
    QmuxProtocolError,
    is_graceful_termination,
    library_error_name,
)
from webtransport.webtransport_ext import qmux

__all__ = ["ConnectionDriver"]

# 送受信ループのポーリング間隔 (秒)。送信待ちとタイマーをこの間隔で処理する
POLL_INTERVAL = 0.05

# 一度に読み取る最大バイト数 (1 レコードの最大長 16 KiB より十分大きく取る)
READ_SIZE = 65536

# トランスポートが切れたことを表す例外
_TRANSPORT_ERRORS = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)


class ConnectionDriver:
    """1 つの QMux 接続を asyncio の StreamReader / StreamWriter 上で駆動する"""

    def __init__(
        self,
        connection: qmux.Connection,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._connection = connection
        self._reader = reader
        self._writer = writer
        self._lock = asyncio.Lock()
        self._closed = False
        self._ready = False
        # コールバック (設定されていないものは呼ばない)
        self.on_ready: Callable[[], Awaitable[None]] | None = None
        self.on_closed: Callable[[], Awaitable[None]] | None = None
        self.on_stream_data: Callable[[int, bytes, bool], Awaitable[None]] | None = None
        self.on_stream_reset: Callable[[int, int], Awaitable[None]] | None = None
        self.on_stop_sending: Callable[[int, int], Awaitable[None]] | None = None

    @property
    def connection(self) -> qmux.Connection:
        """駆動している Sans-IO の接続"""
        return self._connection

    @property
    def is_closed(self) -> bool:
        """接続が閉じているかどうか"""
        return self._closed

    @property
    def is_ready(self) -> bool:
        """ピアのトランスポートパラメータを受け取ったかどうか"""
        return self._ready

    @property
    def peer_address(self) -> tuple[str, int] | None:
        """接続先のアドレス"""
        peer = self._writer.get_extra_info("peername")
        if isinstance(peer, tuple) and len(peer) >= 2:
            return (str(peer[0]), int(peer[1]))
        return None

    @property
    def negotiated_alpn_protocol(self) -> str | None:
        """ALPN で選択されたプロトコル (TLS を使っていない場合は None)"""
        ssl_object = self._writer.get_extra_info("ssl_object")
        if ssl_object is None:
            return None
        return ssl_object.selected_alpn_protocol()

    async def wait_ready(self, timeout: float) -> None:
        """ピアのトランスポートパラメータを受け取るまで待つ (QMux のハンドシェイク完了)

        Args:
            timeout: 待ち時間の上限 (秒)

        Raises:
            QmuxConnectionError: タイムアウトした場合、または途中で接続が閉じた場合
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not self._ready:
            if loop.time() >= deadline:
                raise QmuxConnectionError("QMux handshake timed out")
            if not await self._pump_once():
                raise QmuxConnectionError("QMux connection closed during the handshake")

    async def open_stream(self, unidirectional: bool = False) -> int:
        """ストリームを開く

        Args:
            unidirectional: 単方向ストリームを開くかどうか

        Returns:
            ストリーム ID。開けなかった場合は -1
        """
        async with self._lock:
            if self._closed:
                raise QmuxConnectionError("QMux connection is closed")
            stream_id = self._connection.open_stream(not unidirectional)
            await self._flush_locked()
            return stream_id

    async def send_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """ストリームデータを送る

        Args:
            stream_id: 対象のストリーム ID
            data: 送るバイト列
            fin: 送信側を閉じるかどうか
        """
        async with self._lock:
            if self._closed:
                raise QmuxConnectionError("QMux connection is closed")
            self._connection.send_stream_data(stream_id, data, fin)
            await self._flush_locked()

    async def close(self, error_code: int = 0, reason: str = "") -> None:
        """CONNECTION_CLOSE を送って接続を閉じる

        Args:
            error_code: 送るエラーコード
            reason: 理由の文字列
        """
        async with self._lock:
            if self._closed:
                return
            self._connection.close(error_code, reason)
            await self._flush_locked()
            self._closed = True
        await self._close_transport()

    async def run(self) -> None:
        """受信ループ

        ピアがトランスポートを閉じるか、`close()` が呼ばれるまで回る。トランス
        ポートが切れた場合は `QmuxConnectionError`、dwnx がプロトコルエラーを
        返した場合は `QmuxProtocolError` を送出する。
        """
        try:
            while not self._closed:
                if not await self._pump_once():
                    # ピアがトランスポートを閉じた
                    break
        except _TRANSPORT_ERRORS as exc:
            raise QmuxConnectionError(f"QMux connection lost: {exc}") from exc
        finally:
            self._closed = True
            await self._close_transport()
            if self.on_closed is not None:
                await self.on_closed()

    async def _close_transport(self) -> None:
        """トランスポートを閉じる"""
        if self._writer.is_closing():
            return
        self._writer.close()
        try:
            await self._writer.wait_closed()
        except _TRANSPORT_ERRORS:
            pass

    async def _flush_locked(self) -> None:
        """送信すべきレコードを書き出す (ロックを保持した状態で呼ぶ)"""
        while (record := self._connection.pending_record) is not None:
            self._writer.write(record)
        await self._writer.drain()

    async def _pump_once(self) -> bool:
        """読み書きとタイマーを 1 巡させる

        Returns:
            続行する場合は True、ピアがトランスポートを閉じた場合は False
        """
        async with self._lock:
            await self._flush_locked()
            timeout_ns = self._connection.timeout

        wait: float | None = POLL_INTERVAL
        if timeout_ns is not None:
            if timeout_ns == 0:
                # タイマー満了。読み取りを待たずに処理する
                wait = None
            else:
                wait = min(POLL_INTERVAL, timeout_ns / 1_000_000_000)

        received = False
        data = b""
        if wait is not None:
            try:
                data = await asyncio.wait_for(self._reader.read(READ_SIZE), timeout=wait)
                received = True
            except TimeoutError:
                data = b""
        else:
            # タイマーだけを処理する (read() のキャンセルでデータは失われない)
            await asyncio.sleep(0)

        if received and not data:
            # EOF。ピアがトランスポートを閉じた
            return False

        if data:
            async with self._lock:
                result = self._connection.receive(data)
            if result < 0 and not is_graceful_termination(result):
                message = qmux.Connection.strerror(result)
                raise QmuxProtocolError(
                    f"QMux library error: {library_error_name(result)} ({result}) {message}"
                )

        async with self._lock:
            if timeout_ns is not None and self._connection.timeout == 0:
                self._connection.handle_timeout()
            events: list[qmux.Event] = []
            while (event := self._connection.next_event()) is not None:
                events.append(event)

        await self._dispatch(events)
        return True

    async def _dispatch(self, events: list[qmux.Event]) -> None:
        """イベントをコールバックへ配る"""
        for event in events:
            if event.type is qmux.EventType.TRANSPORT_PARAMS_RECEIVED:
                self._ready = True
                if self.on_ready is not None:
                    await self.on_ready()
            elif event.type is qmux.EventType.STREAM_DATA:
                if self.on_stream_data is not None:
                    await self.on_stream_data(event.stream_id, bytes(event.data), event.fin)
            elif event.type is qmux.EventType.STREAM_RESET:
                if self.on_stream_reset is not None:
                    await self.on_stream_reset(event.stream_id, event.error_code)
            elif event.type is qmux.EventType.STOP_SENDING:
                if self.on_stop_sending is not None:
                    await self.on_stop_sending(event.stream_id, event.error_code)
            # STREAM_CLOSED はここでは通知しない (dwnx の完了通知であり、
            # アプリケーションから見たストリームの終端は FIN と RESET で分かる)
