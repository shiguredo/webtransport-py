"""QMux の asyncio サーバー

TCP で待ち受け、`ssl` に `SSLContext` を渡すと TLS 上で動く。TLS を使う場合は
`alpn_protocols` が必須である (draft-ietf-quic-qmux-02 Section 8.1)。
"""

import asyncio
import logging
import ssl as ssl_module
from collections.abc import Awaitable, Callable
from itertools import count
from typing import Self

from webtransport.qmux._connection import ConnectionDriver
from webtransport.qmux.exceptions import QmuxConnectionError
from webtransport.webtransport_ext import qmux

__all__ = ["Server", "Session"]

logger = logging.getLogger(__name__)


class Session:
    """サーバーが受け付けた 1 接続を表すハンドル

    QMux の接続がそのまま多重化の単位であり、WebTransport のようなアプリ
    ケーション層のセッション識別子はプロトコルに無い。そのため `session_id` は
    サーバーが接続ごとに振る連番である。
    """

    def __init__(self, driver: ConnectionDriver, session_id: int) -> None:
        self._driver = driver
        self._session_id = session_id

    @property
    def session_id(self) -> int:
        """サーバーが振った接続の連番"""
        return self._session_id

    @property
    def peer_address(self) -> tuple[str, int] | None:
        """接続元のアドレス"""
        return self._driver.peer_address

    @property
    def is_closed(self) -> bool:
        """接続が閉じているかどうか"""
        return self._driver.is_closed

    async def open_stream(self, unidirectional: bool = False) -> int:
        """ストリームを開く

        Args:
            unidirectional: 単方向ストリームを開くかどうか

        Returns:
            ストリーム ID。開けなかった場合は -1
        """
        return await self._driver.open_stream(unidirectional)

    async def send_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """ストリームデータを送る

        Args:
            stream_id: 対象のストリーム ID
            data: 送るバイト列
            fin: 送信側を閉じるかどうか
        """
        await self._driver.send_stream_data(stream_id, data, fin)

    async def close(self, error_code: int = 0, reason: str = "") -> None:
        """CONNECTION_CLOSE を送って接続を閉じる

        Args:
            error_code: 送るエラーコード
            reason: 理由の文字列
        """
        await self._driver.close(error_code, reason)


class Server:
    """QMux の asyncio サーバー

    Usage:
        server = Server("127.0.0.1", 4433)
        server.on_stream_data(on_stream_data)
        await server.start()
        await server.run()
        await server.stop()
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        config: qmux.Config | None = None,
        ssl: ssl_module.SSLContext | None = None,
        alpn_protocols: list[str] | None = None,
    ) -> None:
        """サーバーを作る

        Args:
            host: 待ち受けるホスト
            port: 待ち受けるポート (0 を指定すると空きポートを使う)
            config: QMux の設定 (省略時は既定値)
            ssl: TLS を使う場合の SSLContext
            alpn_protocols: TLS を使う場合に受け入れる ALPN プロトコル

        Raises:
            ValueError: `ssl` を渡したのに `alpn_protocols` が空の場合、
                または `alpn_protocols` を渡したのに `ssl` が無い場合
        """
        if ssl is not None and not alpn_protocols:
            raise ValueError(
                "alpn_protocols is required when ssl is used (draft-ietf-quic-qmux-02 Section 8.1)"
            )
        if alpn_protocols and ssl is None:
            raise ValueError("alpn_protocols requires ssl")
        if ssl is not None and alpn_protocols is not None:
            ssl.set_alpn_protocols(alpn_protocols)

        self._host = host
        self._port = port
        self._config = config if config is not None else qmux.Config()
        self._ssl = ssl
        self._alpn_protocols = list(alpn_protocols) if alpn_protocols else []
        self._server: asyncio.Server | None = None
        self._actual_port = 0
        self._session_ids = count(1)
        self._sessions: dict[int, Session] = {}
        self._callbacks: dict[str, Callable[..., Awaitable[None]] | None] = {
            "session_ready": None,
            "session_closed": None,
            "stream_data": None,
            "stream_reset": None,
            "stop_sending": None,
        }

    @property
    def host(self) -> str:
        """待ち受けているホスト"""
        return self._host

    @property
    def port(self) -> int:
        """指定したポート"""
        return self._port

    @property
    def actual_port(self) -> int:
        """実際に待ち受けているポート (start() 前は 0)"""
        return self._actual_port

    @property
    def is_running(self) -> bool:
        """待ち受けているかどうか"""
        return self._server is not None

    @property
    def sessions(self) -> tuple[Session, ...]:
        """接続中のセッション"""
        return tuple(self._sessions.values())

    def on_session_ready(self, callback: Callable[[Session], Awaitable[None]]) -> None:
        """接続が確立したときに呼ばれるコールバックを登録する"""
        self._callbacks["session_ready"] = callback

    def on_session_closed(self, callback: Callable[[Session], Awaitable[None]]) -> None:
        """接続が閉じたときに呼ばれるコールバックを登録する"""
        self._callbacks["session_closed"] = callback

    def on_stream_data(
        self,
        callback: Callable[[Session, int, bytes, bool], Awaitable[None]],
    ) -> None:
        """ストリームデータを受信したときに呼ばれるコールバックを登録する

        Args:
            callback: `(session, stream_id, data, fin)` を受け取るコルーチン
        """
        self._callbacks["stream_data"] = callback

    def on_stream_reset(
        self,
        callback: Callable[[Session, int, int], Awaitable[None]],
    ) -> None:
        """ピアがストリームをリセットしたときに呼ばれるコールバックを登録する

        Args:
            callback: `(session, stream_id, error_code)` を受け取るコルーチン
        """
        self._callbacks["stream_reset"] = callback

    def on_stop_sending(
        self,
        callback: Callable[[Session, int, int], Awaitable[None]],
    ) -> None:
        """ピアが送信の停止を要求したときに呼ばれるコールバックを登録する

        Args:
            callback: `(session, stream_id, error_code)` を受け取るコルーチン
        """
        self._callbacks["stop_sending"] = callback

    async def start(self) -> None:
        """待ち受けを開始する

        Raises:
            RuntimeError: すでに開始している場合
        """
        if self._server is not None:
            raise RuntimeError("server is already started")
        self._server = await asyncio.start_server(
            self._on_connection,
            self._host,
            self._port,
            ssl=self._ssl,
        )
        sockets = self._server.sockets
        if sockets:
            self._actual_port = int(sockets[0].getsockname()[1])

    async def run(self) -> None:
        """接続を受け付け続ける

        `stop()` でサーバーが閉じられるまでブロックする。
        """
        if self._server is None:
            await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def stop(self) -> None:
        """待ち受けを停止し、接続を閉じる"""
        if self._server is None:
            return
        # wait_closed() は接続の終了を待つため、先に接続を閉じる
        self._server.close_clients()
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._sessions.clear()

    async def _on_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """接続を受け付けたときの処理 (接続ごとに 1 タスク)"""
        if self._ssl is not None:
            ssl_object = writer.get_extra_info("ssl_object")
            selected = ssl_object.selected_alpn_protocol() if ssl_object is not None else None
            if selected is None:
                # draft Section 8.1 は ALPN が合意できない場合にハンドシェイクを
                # 中断することを MUST としている。TLS ハンドシェイクは完了して
                # しまっているため、ここで接続を閉じる
                logger.warning("ALPN negotiation failed: closing the connection")
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionResetError, BrokenPipeError:
                    pass
                return

        session_id = next(self._session_ids)
        connection = qmux.Connection.create_server(self._config)
        driver = ConnectionDriver(connection, reader, writer)
        session = Session(driver, session_id)
        self._sessions[session_id] = session
        self._wire_callbacks(driver, session)
        try:
            await driver.run()
        except QmuxConnectionError as exc:
            logger.warning("qmux connection error (session_id=%d): %s", session_id, exc)
        finally:
            self._sessions.pop(session_id, None)

    def _wire_callbacks(self, driver: ConnectionDriver, session: Session) -> None:
        """コールバックをドライバへ配線する"""
        session_ready = self._callbacks["session_ready"]
        if session_ready is not None:

            async def on_ready() -> None:
                await session_ready(session)

            driver.on_ready = on_ready

        session_closed = self._callbacks["session_closed"]
        if session_closed is not None:

            async def on_closed() -> None:
                await session_closed(session)

            driver.on_closed = on_closed

        stream_data = self._callbacks["stream_data"]
        if stream_data is not None:

            async def on_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
                await stream_data(session, stream_id, data, fin)

            driver.on_stream_data = on_stream_data

        stream_reset = self._callbacks["stream_reset"]
        if stream_reset is not None:

            async def on_stream_reset(stream_id: int, error_code: int) -> None:
                await stream_reset(session, stream_id, error_code)

            driver.on_stream_reset = on_stream_reset

        stop_sending = self._callbacks["stop_sending"]
        if stop_sending is not None:

            async def on_stop_sending(stream_id: int, error_code: int) -> None:
                await stop_sending(session, stream_id, error_code)

            driver.on_stop_sending = on_stop_sending

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.stop()
