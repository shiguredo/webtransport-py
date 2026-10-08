"""QMux の asyncio クライアント

TCP で接続し、`ssl` に `SSLContext` を渡すと TLS 上で動く。TLS を使う場合は
`alpn_protocols` が必須である (draft-ietf-quic-qmux-02 Section 8.1 が QMux over
TLS でアプリケーションプロトコルを合意する手段として ALPN を MUST としている)。
"""

from __future__ import annotations

import asyncio
import ssl as ssl_module
from collections.abc import Awaitable, Callable
from typing import Self

from webtransport.qmux._connection import ConnectionDriver
from webtransport.qmux.exceptions import QmuxConnectionError
from webtransport.webtransport_ext import qmux

__all__ = ["Client"]


class Client:
    """QMux の asyncio クライアント

    Usage:
        client = Client("127.0.0.1", 4433)
        client.on_stream_data(on_stream_data)
        await client.connect()
        stream_id = await client.open_stream()
        await client.send_stream_data(stream_id, b"hello", fin=True)
        await client.run()
        await client.close()
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        config: qmux.Config | None = None,
        ssl: ssl_module.SSLContext | None = None,
        alpn_protocols: list[str] | None = None,
        close_reason: str = "",
    ) -> None:
        """クライアントを作る

        Args:
            host: 接続先のホスト
            port: 接続先のポート
            config: QMux の設定 (省略時は既定値)
            ssl: TLS を使う場合の SSLContext
            alpn_protocols: TLS を使う場合に提示する ALPN プロトコル
            close_reason: `close()` で送る理由の文字列

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
            # ALPN は SSLContext 側に設定する (asyncio の接続 API には
            # alpn_protocols 引数が無い)
            ssl.set_alpn_protocols(alpn_protocols)

        self._host = host
        self._port = port
        self._config = config if config is not None else qmux.Config()
        self._ssl = ssl
        self._alpn_protocols = list(alpn_protocols) if alpn_protocols else []
        self._close_reason = close_reason
        self._driver: ConnectionDriver | None = None
        self._callbacks: dict[str, Callable[..., Awaitable[None]] | None] = {
            "connected": None,
            "closed": None,
            "stream_data": None,
            "stream_reset": None,
            "stop_sending": None,
        }

    @property
    def host(self) -> str:
        """接続先のホスト"""
        return self._host

    @property
    def port(self) -> int:
        """接続先のポート"""
        return self._port

    @property
    def is_connected(self) -> bool:
        """接続中かどうか"""
        return self._driver is not None and not self._driver.is_closed

    @property
    def peer_address(self) -> tuple[str, int] | None:
        """接続先のアドレス (未接続なら None)"""
        if self._driver is None:
            return None
        return self._driver.peer_address

    @property
    def negotiated_alpn_protocol(self) -> str | None:
        """ALPN で選択されたプロトコル (TLS を使っていない場合は None)"""
        if self._driver is None:
            return None
        return self._driver.negotiated_alpn_protocol

    def on_connected(self, callback: Callable[[], Awaitable[None]]) -> None:
        """トランスポートパラメータの交換が終わったときに呼ばれるコールバックを登録する"""
        self._callbacks["connected"] = callback

    def on_closed(self, callback: Callable[[], Awaitable[None]]) -> None:
        """接続が閉じたときに呼ばれるコールバックを登録する"""
        self._callbacks["closed"] = callback

    def on_stream_data(
        self,
        callback: Callable[[int, bytes, bool], Awaitable[None]],
    ) -> None:
        """ストリームデータを受信したときに呼ばれるコールバックを登録する

        Args:
            callback: `(stream_id, data, fin)` を受け取るコルーチン
        """
        self._callbacks["stream_data"] = callback

    def on_stream_reset(
        self,
        callback: Callable[[int, int], Awaitable[None]],
    ) -> None:
        """ピアがストリームをリセットしたときに呼ばれるコールバックを登録する

        Args:
            callback: `(stream_id, error_code)` を受け取るコルーチン
        """
        self._callbacks["stream_reset"] = callback

    def on_stop_sending(
        self,
        callback: Callable[[int, int], Awaitable[None]],
    ) -> None:
        """ピアが送信の停止を要求したときに呼ばれるコールバックを登録する

        Args:
            callback: `(stream_id, error_code)` を受け取るコルーチン
        """
        self._callbacks["stop_sending"] = callback

    async def connect(self, timeout: float = 10.0) -> None:
        """接続し、トランスポートパラメータの交換が終わるまで待つ

        Args:
            timeout: 接続とハンドシェイクの上限 (秒)

        Raises:
            RuntimeError: `connect()` を二度呼んだ場合
            QmuxConnectionError: 接続に失敗した場合、またはハンドシェイクが
                タイムアウトした場合
        """
        if self._driver is not None:
            raise RuntimeError("connect() has already been called")
        connection = qmux.Connection.create_client(self._config)
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self._host,
                    self._port,
                    ssl=self._ssl,
                    server_hostname=self._host if self._ssl is not None else None,
                ),
                timeout=timeout,
            )
        except TimeoutError as exc:
            raise QmuxConnectionError(f"connection to {self._host}:{self._port} timed out") from exc
        except OSError as exc:
            raise QmuxConnectionError(
                f"failed to connect to {self._host}:{self._port}: {exc}"
            ) from exc

        driver = ConnectionDriver(connection, reader, writer)
        self._driver = driver
        self._wire_callbacks(driver)

        # サーバーと同じく、ALPN が交渉されていない接続は使わない
        if self._ssl is not None and driver.negotiated_alpn_protocol is None:
            await driver.close()
            self._driver = None
            raise QmuxConnectionError(
                "ALPN negotiation failed: the server did not select an application protocol"
            )
        try:
            await driver.wait_ready(timeout)
        except QmuxConnectionError:
            await driver.close()
            self._driver = None
            raise

    def _wire_callbacks(self, driver: ConnectionDriver) -> None:
        """コールバックをドライバへ配線する"""
        connected = self._callbacks["connected"]
        if connected is not None:

            async def on_ready() -> None:
                await connected()

            driver.on_ready = on_ready

        closed = self._callbacks["closed"]
        if closed is not None:

            async def on_closed() -> None:
                await closed()

            driver.on_closed = on_closed

        stream_data = self._callbacks["stream_data"]
        if stream_data is not None:

            async def on_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
                await stream_data(stream_id, data, fin)

            driver.on_stream_data = on_stream_data

        stream_reset = self._callbacks["stream_reset"]
        if stream_reset is not None:

            async def on_stream_reset(stream_id: int, error_code: int) -> None:
                await stream_reset(stream_id, error_code)

            driver.on_stream_reset = on_stream_reset

        stop_sending = self._callbacks["stop_sending"]
        if stop_sending is not None:

            async def on_stop_sending(stream_id: int, error_code: int) -> None:
                await stop_sending(stream_id, error_code)

            driver.on_stop_sending = on_stop_sending

    async def run(self) -> None:
        """受信ループ

        ピアが接続を閉じるか `close()` を呼ぶまでブロックする。
        """
        if self._driver is None:
            raise RuntimeError("connect() must be called before run()")
        await self._driver.run()

    async def close(self) -> None:
        """CONNECTION_CLOSE を送って接続を閉じる"""
        if self._driver is None:
            return
        await self._driver.close(reason=self._close_reason)

    async def open_stream(self, unidirectional: bool = False) -> int:
        """ストリームを開く

        Args:
            unidirectional: 単方向ストリームを開くかどうか

        Returns:
            ストリーム ID。開けなかった場合は -1
        """
        return await self._require_driver().open_stream(unidirectional)

    async def send_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """ストリームデータを送る

        Args:
            stream_id: 対象のストリーム ID
            data: 送るバイト列
            fin: 送信側を閉じるかどうか
        """
        await self._require_driver().send_stream_data(stream_id, data, fin)

    def _require_driver(self) -> ConnectionDriver:
        """ドライバを取り出す (未接続なら例外)"""
        if self._driver is None:
            raise RuntimeError("connect() must be called before this operation")
        return self._driver

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.close()
