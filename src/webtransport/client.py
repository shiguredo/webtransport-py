"""WebTransport の統一クライアント

WebTransport over HTTP/3 と WebTransport over HTTP/2 を `HTTPVersion` enum で
選べるようにする。プロトコル固有の API (`migrate` / `close_stream` /
`stop_sending` / `on_error` など) は `h3` / `h2` プロパティから実装
(`webtransport.h3.Client` / `webtransport.h2.Client`) へ降りて呼ぶ。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from webtransport.exceptions import WebTransportError
from webtransport.http_version import HTTPVersion

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from webtransport.h2 import Client as H2Client
    from webtransport.h2 import Config as H2Config
    from webtransport.h3 import Client as H3Client
    from webtransport.quic import Config as QuicConfig

__all__ = ["Client"]

# h3 クライアントのアイドルタイムアウトの既定値。h2 を選んだときに h3 固有の
# 引数が渡されたかを判定するために持つ
DEFAULT_IDLE_TIMEOUT_NS = 30_000_000_000


class Client:
    """WebTransport クライアント (HTTP バージョンを enum で選ぶ)

    Usage:
        from webtransport import Client, HTTPVersion

        client = Client(
            url="https://localhost:4433/webtransport",
            http_version=HTTPVersion.HTTP3,
        )
        await client.connect()
        await client.send_datagram(b"Hello")
        await client.close()

    `http_version` を省略すると HTTP/3 になる。プロトコル固有の API は
    `client.h3` / `client.h2` から呼ぶ (選択していない側は None)。
    """

    def __init__(
        self,
        url: str,
        http_version: HTTPVersion = HTTPVersion.HTTP3,
        verify_peer: bool = True,
        origin: str = "",
        close_wait_timeout: float = 3.0,
        idle_timeout_ns: int = DEFAULT_IDLE_TIMEOUT_NS,
        ca_file: str | None = None,
        verify_callback: Callable[[list[bytes]], bool] | None = None,
        quic_config: QuicConfig | None = None,
        config: H2Config | None = None,
    ) -> None:
        """クライアントを初期化する

        Args:
            url: WebTransport エンドポイント URL (`https://host:port/path`)
            http_version: 使う HTTP バージョン (既定は HTTP/3)
            verify_peer: サーバー証明書を検証するかどうか
            origin: Origin ヘッダー値 (空なら付与しない)
            close_wait_timeout: close() 時に CONNECT ストリームのピア側
                終了を待つ上限 (秒)。0 以下では待たない
            idle_timeout_ns: アイドルタイムアウト (ナノ秒。HTTP/3 のみ)
            ca_file: CA 証明書ファイルパス (HTTP/3 のみ)
            verify_callback: ピア証明書検証コールバック (HTTP/3 のみ)
            quic_config: QUIC 設定 (HTTP/3 のみ)
            config: WebTransport over HTTP/2 の設定 (HTTP/2 のみ)

        Raises:
            ValueError: 選択した HTTP バージョンと矛盾する引数を渡した場合
        """
        self._http_version = HTTPVersion(http_version)
        self._h3: H3Client | None = None
        self._h2: H2Client | None = None

        if self._http_version is HTTPVersion.HTTP3:
            if config is not None:
                raise ValueError(
                    "config is only available for WebTransport over HTTP/2 "
                    "(http_version=HTTPVersion.HTTP2)"
                )
            from webtransport.h3 import Client as H3ClientImpl

            self._h3 = H3ClientImpl(
                url=url,
                verify_peer=verify_peer,
                origin=origin,
                idle_timeout_ns=idle_timeout_ns,
                ca_file=ca_file,
                verify_callback=verify_callback,
                quic_config=quic_config,
                close_wait_timeout=close_wait_timeout,
            )
        else:
            if (
                quic_config is not None
                or ca_file is not None
                or verify_callback is not None
                or idle_timeout_ns != DEFAULT_IDLE_TIMEOUT_NS
            ):
                raise ValueError(
                    "idle_timeout_ns / ca_file / verify_callback / quic_config are "
                    "only available for WebTransport over HTTP/3 "
                    "(http_version=HTTPVersion.HTTP3)"
                )
            from webtransport.h2 import Client as H2ClientImpl

            self._h2 = H2ClientImpl(
                url=url,
                verify_peer=verify_peer,
                origin=origin,
                config=config,
                close_wait_timeout=close_wait_timeout,
            )

    @property
    def http_version(self) -> HTTPVersion:
        """選択した HTTP バージョン"""
        return self._http_version

    @property
    def h3(self) -> H3Client | None:
        """WebTransport over HTTP/3 の実装 (HTTP/2 を選んだ場合は None)"""
        return self._h3

    @property
    def h2(self) -> H2Client | None:
        """WebTransport over HTTP/2 の実装 (HTTP/3 を選んだ場合は None)"""
        return self._h2

    @property
    def url(self) -> str:
        """エンドポイント URL"""
        return self._impl().url

    @property
    def host(self) -> str:
        """接続先ホスト"""
        return self._impl().host

    @property
    def port(self) -> int:
        """接続先ポート"""
        return self._impl().port

    @property
    def is_connected(self) -> bool:
        """接続済みかどうか"""
        return self._impl().is_connected

    @property
    def session_id(self) -> int:
        """WebTransport セッション ID (未確立は -1)"""
        return self._impl().session_id

    async def connect(self, timeout: float = 10.0) -> None:
        """サーバーに接続し、WebTransport セッションを確立する

        Raises:
            WebTransportError: 失敗した場合。原因となった層の例外
                (`webtransport.quic.exceptions` など) を送出する
        """
        await self._impl().connect(timeout)

    async def run(self) -> None:
        """受信ループを実行する

        接続またはセッションがエラーで終了した場合は、その原因の例外を
        送出して終了する。
        """
        await self._impl().run()

    async def close(self) -> None:
        """セッションと接続を閉じる"""
        await self._impl().close()

    async def open_stream(self, unidirectional: bool = False) -> int:
        """WebTransport ストリームを開く

        Returns:
            ストリーム ID。失敗した場合は -1
        """
        return await self._impl().open_stream(unidirectional)

    async def send_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """ストリームへデータを送信する"""
        await self._impl().send_stream_data(stream_id, data, fin)

    async def send_datagram(self, data: bytes) -> None:
        """データグラムを送信する"""
        await self._impl().send_datagram(data)

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセットする"""
        await self._impl().reset_stream(stream_id, error_code)

    def on_session_ready(self, callback: Callable[[int], Awaitable[None]]) -> None:
        """セッション確立時のコールバックを設定する"""
        self._impl().on_session_ready(callback)

    def on_session_closed(self, callback: Callable[[int], Awaitable[None]]) -> None:
        """セッション終了時のコールバックを設定する"""
        self._impl().on_session_closed(callback)

    def on_stream_data(self, callback: Callable[[int, bytes], Awaitable[None]]) -> None:
        """ストリームデータ受信時のコールバックを設定する"""
        self._impl().on_stream_data(callback)

    def on_stream_reset(self, callback: Callable[[int, int | None], Awaitable[None]]) -> None:
        """ストリームのリセット受信時のコールバックを設定する

        HTTP/2 ではエラーコードが常に int になる (HTTP/2 層のリセットは
        アプリケーションエラーコードを持つ)。
        """
        self._impl().on_stream_reset(callback)

    def on_datagram(self, callback: Callable[[bytes], Awaitable[None]]) -> None:
        """データグラム受信時のコールバックを設定する"""
        self._impl().on_datagram(callback)

    def on_goaway(self, callback: Callable[..., Awaitable[None]]) -> None:
        """GOAWAY 受信時のコールバックを設定する

        コールバックの引数は HTTP バージョンで異なる。HTTP/3 は
        `(goaway_id)`、HTTP/2 は `(last_stream_id, error_code)`。
        """
        self._impl().on_goaway(callback)

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.close()

    def _impl(self) -> H3Client | H2Client:
        """選択した HTTP バージョンの実装を返す"""
        impl = self._h3 if self._h3 is not None else self._h2
        if impl is None:
            raise WebTransportError("client implementation is missing")
        return impl
