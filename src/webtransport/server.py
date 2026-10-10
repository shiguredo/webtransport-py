"""WebTransport の統一サーバー

WebTransport over HTTP/3 と WebTransport over HTTP/2 を `HTTPVersion` enum で
選べるようにする。コールバックはセッションハンドル (`Session`) を受け取り、
プロトコルによらず同じ形でセッションを操作する。プロトコル固有の操作は
`webtransport.h3.Server` / `webtransport.h2.Server` のハンドルへ降りて呼ぶ。
"""

from typing import TYPE_CHECKING, Any, Self

from webtransport.exceptions import WebTransportError
from webtransport.http_version import HTTPVersion
from webtransport.quic import Config

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from webtransport._h2_server import Server as H2Server
    from webtransport._h3_server import Server as H3Server
    from webtransport.h2 import Config as H2Config

__all__ = ["Server", "Session"]

DEFAULT_IDLE_TIMEOUT_NS = 30_000_000_000


class Session:
    """サーバー側の WebTransport セッション (プロトコル非依存のハンドル)

    コールバックへ渡される。プロトコルによらず使える操作だけを持ち、固有の操作は
    具象クラス (`webtransport.h3.Server` / `webtransport.h2.Server` が作る) に
    生えている。

    Attributes:
        session_id: WebTransport セッション ID
        addr: 接続元アドレス。取得できない場合は None
        http_version: 使っている HTTP バージョン
    """

    def __init__(
        self,
        http_version: HTTPVersion,
        session_id: int,
        addr: tuple[str, int] | None,
    ) -> None:
        self._http_version = http_version
        self._session_id = session_id
        self._addr = addr

    @property
    def session_id(self) -> int:
        """WebTransport セッション ID"""
        return self._session_id

    @property
    def addr(self) -> tuple[str, int] | None:
        """接続元アドレス (取得できない場合は None)"""
        return self._addr

    @property
    def http_version(self) -> HTTPVersion:
        """使っている HTTP バージョン"""
        return self._http_version

    async def open_stream(self, unidirectional: bool = True) -> int:
        """セッションのストリームを開く

        Returns:
            ストリーム ID。失敗した場合は -1
        """
        raise NotImplementedError

    async def send_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """ストリームへデータを送信する"""
        raise NotImplementedError

    async def send_datagram(self, data: bytes) -> None:
        """データグラムを送信する"""
        raise NotImplementedError

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセットする"""
        raise NotImplementedError

    async def stop_sending(self, stream_id: int, error_code: int = 0) -> None:
        """ピアに送信停止を要求する"""
        raise NotImplementedError

    async def close_session(self, error_code: int = 0, error_message: str = "") -> None:
        """セッションを終了コードと理由付きで閉じる

        接続は閉じず、この WebTransport セッションだけを終了する。
        """
        raise NotImplementedError


class _H3Session(Session):
    """WebTransport over HTTP/3 のセッションハンドル

    h3 のサーバーは接続元アドレスとセッション ID を鍵にするため、両方を保持して
    送信のたびに渡す非対称をここで吸収する。
    """

    def __init__(self, server: H3Server, addr: tuple[str, int], session_id: int) -> None:
        super().__init__(HTTPVersion.HTTP3, session_id, addr)
        self._server = server
        # 送信のたびに使うため、None になり得ない型で保持する
        self._h3_addr = addr

    async def open_stream(self, unidirectional: bool = True) -> int:
        return await self._server.open_stream(self._h3_addr, self._session_id, unidirectional)

    async def send_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        await self._server.send_stream_data(self._h3_addr, stream_id, data, fin)

    async def send_datagram(self, data: bytes) -> None:
        await self._server.send_datagram(self._h3_addr, self._session_id, data)

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        await self._server.reset_stream(self._h3_addr, stream_id, error_code)

    async def close_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームを閉じる (HTTP/3 のみ)

        `reset_stream` と同じく RESET_STREAM を送出する。
        """
        await self._server.close_stream(self._h3_addr, stream_id, error_code)

    async def stop_sending(self, stream_id: int, error_code: int = 0) -> None:
        await self._server.stop_sending(self._h3_addr, stream_id, error_code)

    async def close_session(self, error_code: int = 0, error_message: str = "") -> None:
        await self._server.close_session(
            self._h3_addr,
            self._session_id,
            error_code,
            error_message,
        )


class _H2Session(Session):
    """WebTransport over HTTP/2 のセッションハンドル

    `webtransport.h2.Server` が作る `SessionWriter` を包む。TCP の接続元アドレスは
    writer から取得する。
    """

    def __init__(self, writer: Any) -> None:
        super().__init__(HTTPVersion.HTTP2, writer.session_id, writer.addr)
        self._writer = writer

    async def open_stream(self, unidirectional: bool = True) -> int:
        return await self._writer.open_stream(unidirectional)

    async def send_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        await self._writer.send_stream_data(stream_id, data, fin)

    async def send_datagram(self, data: bytes) -> None:
        await self._writer.send_datagram(data)

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        await self._writer.reset_stream(stream_id, error_code)

    async def stop_sending(self, stream_id: int, error_code: int = 0) -> None:
        """ピアに送信停止を要求する"""
        await self._writer.stop_sending(stream_id, error_code)

    async def close_session(self, error_code: int = 0, error_message: str = "") -> None:
        """セッションを終了コードと理由付きで閉じる"""
        await self._writer.close_session(error_code, error_message)


class Server:
    """WebTransport サーバー (HTTP バージョンを enum で選ぶ)

    Usage:
        from webtransport import HTTPVersion, Server

        server = Server(
            host="0.0.0.0",
            port=4433,
            certfile="cert.pem",
            keyfile="key.pem",
            http_version=HTTPVersion.HTTP3,
        )

        async def on_session_ready(session: Session) -> None:
            stream_id = await session.open_stream()
            await session.send_stream_data(stream_id, b"Hello")

        server.on_session_ready(on_session_ready)
        await server.start()
        await server.run()
    """

    def __init__(
        self,
        host: str,
        port: int,
        http_version: HTTPVersion = HTTPVersion.HTTP3,
        certfile: str | None = None,
        keyfile: str | None = None,
        allowed_origins: list[str] | None = None,
        idle_timeout_ns: int = DEFAULT_IDLE_TIMEOUT_NS,
        quic_config: Config | None = None,
        config: H2Config | None = None,
    ) -> None:
        """サーバーを初期化する

        Args:
            host: バインドするホスト
            port: バインドするポート (0 で空きポートを自動選択)
            http_version: 使う HTTP バージョン (既定は HTTP/3)
            certfile: 証明書ファイルパス (HTTP/2 では必須)
            keyfile: 秘密鍵ファイルパス (HTTP/2 では必須)
            allowed_origins: 許可する Origin の一覧 (None と空リストは全許可)
            idle_timeout_ns: アイドルタイムアウト (ナノ秒。HTTP/3 のみ)
            quic_config: QUIC 設定 (HTTP/3 のみ)
            config: WebTransport over HTTP/2 の設定 (HTTP/2 のみ)

        Raises:
            ValueError: 選択した HTTP バージョンと矛盾する引数を渡した場合
        """
        self._http_version = HTTPVersion(http_version)
        self._h3: H3Server | None = None
        self._h2: H2Server | None = None

        if self._http_version is HTTPVersion.HTTP3:
            if config is not None:
                raise ValueError(
                    "config is only available for WebTransport over HTTP/2 "
                    "(http_version=HTTPVersion.HTTP2)"
                )
            from webtransport._h3_server import Server as H3ServerImpl

            self._h3 = H3ServerImpl(
                host=host,
                port=port,
                certfile=certfile,
                keyfile=keyfile,
                idle_timeout_ns=idle_timeout_ns,
                allowed_origins=allowed_origins,
                quic_config=quic_config,
            )
        else:
            if quic_config is not None or idle_timeout_ns != DEFAULT_IDLE_TIMEOUT_NS:
                raise ValueError(
                    "idle_timeout_ns / quic_config are only available for "
                    "WebTransport over HTTP/3 (http_version=HTTPVersion.HTTP3)"
                )
            if certfile is None or keyfile is None:
                raise ValueError(
                    "certfile and keyfile are required for WebTransport over HTTP/2 "
                    "(http_version=HTTPVersion.HTTP2)"
                )
            from webtransport._h2_server import Server as H2ServerImpl

            self._h2 = H2ServerImpl(
                host=host,
                port=port,
                certfile=certfile,
                keyfile=keyfile,
                config=config,
                allowed_origins=allowed_origins,
            )

    @property
    def http_version(self) -> HTTPVersion:
        """選択した HTTP バージョン"""
        return self._http_version

    @property
    def h3(self) -> H3Server | None:
        """WebTransport over HTTP/3 の実装 (HTTP/2 を選んだ場合は None)"""
        return self._h3

    @property
    def h2(self) -> H2Server | None:
        """WebTransport over HTTP/2 の実装 (HTTP/3 を選んだ場合は None)"""
        return self._h2

    @property
    def host(self) -> str:
        """バインドしたホスト"""
        return self._impl().host

    @property
    def port(self) -> int:
        """バインドしたポート"""
        return self._impl().port

    @property
    def actual_port(self) -> int:
        """実際にバインドされたポート"""
        return self._impl().actual_port

    @property
    def is_running(self) -> bool:
        """起動中かどうか"""
        return self._impl().is_running

    def on_session_request(
        self, callback: Callable[[int, list[tuple[str, str]], Any], Awaitable[int | None]]
    ) -> None:
        """セッション要求のコールバックを設定する

        引数は `(session_id, headers, addr)`。None または 200-299 を返すと
        セッションを受理し、300-599 を返すとその status で拒否する。
        """
        self._impl().on_session_request(callback)

    def on_session_ready(self, callback: Callable[[Session], Awaitable[None]]) -> None:
        """セッション確立のコールバックを設定する"""
        if self._h3 is not None:
            # クロージャから参照するため、None 検査済みの値をローカルへ束縛する
            h3_server = self._h3
            h3_server.on_session_ready(
                lambda session_id, addr: callback(_H3Session(h3_server, addr, session_id))
            )
        elif self._h2 is not None:
            self._h2.on_session_ready(lambda writer: callback(_H2Session(writer)))

    def on_session_closed(self, callback: Callable[[Session], Awaitable[None]]) -> None:
        """セッション終了のコールバックを設定する"""
        if self._h3 is not None:
            # クロージャから参照するため、None 検査済みの値をローカルへ束縛する
            h3_server = self._h3
            h3_server.on_session_closed(
                lambda session_id, addr: callback(_H3Session(h3_server, addr, session_id))
            )
        elif self._h2 is not None:
            self._h2.on_session_closed(lambda writer: callback(_H2Session(writer)))

    def on_stream_data(self, callback: Callable[[Session, int, bytes], Awaitable[None]]) -> None:
        """ストリームデータ受信のコールバックを設定する

        引数は `(session, stream_id, data)`。
        """
        if self._h3 is not None:
            # クロージャから参照するため、None 検査済みの値をローカルへ束縛する
            h3_server = self._h3
            h3_server.on_stream_data(
                lambda session_id, stream_id, data, addr: callback(
                    _H3Session(h3_server, addr, session_id), stream_id, data
                )
            )
        elif self._h2 is not None:
            self._h2.on_stream_data(
                lambda stream_id, data, writer: callback(_H2Session(writer), stream_id, data)
            )

    def on_stream_reset(
        self, callback: Callable[[Session, int, int | None], Awaitable[None]]
    ) -> None:
        """ストリームのリセット受信のコールバックを設定する

        引数は `(session, stream_id, error_code)`。HTTP/3 のエラーコードはレンジ外の
        とき None になり得る。
        """
        if self._h3 is not None:
            # クロージャから参照するため、None 検査済みの値をローカルへ束縛する
            h3_server = self._h3
            h3_server.on_stream_reset(
                lambda session_id, stream_id, error_code, addr: callback(
                    _H3Session(h3_server, addr, session_id), stream_id, error_code
                )
            )
        elif self._h2 is not None:
            self._h2.on_stream_reset(
                lambda stream_id, error_code, writer: callback(
                    _H2Session(writer), stream_id, error_code
                )
            )

    def on_datagram(self, callback: Callable[[Session, bytes], Awaitable[None]]) -> None:
        """データグラム受信のコールバックを設定する

        引数は `(session, data)`。
        """
        if self._h3 is not None:
            # クロージャから参照するため、None 検査済みの値をローカルへ束縛する
            h3_server = self._h3
            h3_server.on_datagram(
                lambda session_id, data, addr: callback(
                    _H3Session(h3_server, addr, session_id), data
                )
            )
        elif self._h2 is not None:
            self._h2.on_datagram(lambda data, writer: callback(_H2Session(writer), data))

    def on_goaway(self, callback: Callable[..., Awaitable[None]]) -> None:
        """GOAWAY 受信のコールバックを設定する

        コールバックの引数は HTTP バージョンで異なる。HTTP/3 は
        `(goaway_id, addr)`、HTTP/2 は `(last_stream_id, error_code, addr)`。
        """
        self._impl().on_goaway(callback)

    def on_stop_sending(self, callback: Callable[[Session, int, int], Awaitable[None]]) -> None:
        """送信停止要求のコールバックを設定する (HTTP/2 のみ)

        引数は `(session, stream_id, error_code)`。
        """
        if self._h2 is None:
            raise ValueError(
                "on_stop_sending is only available for WebTransport over HTTP/2 "
                "(http_version=HTTPVersion.HTTP2)"
            )
        self._h2.on_stop_sending(
            lambda stream_id, error_code, writer: callback(
                _H2Session(writer), stream_id, error_code
            )
        )

    def on_error(self, callback: Callable[[Session, int, str], Awaitable[None]]) -> None:
        """プロトコルエラーのコールバックを設定する (HTTP/2 のみ)

        引数は `(session, error_code, error_message)`。
        """
        if self._h2 is None:
            raise ValueError(
                "on_error is only available for WebTransport over HTTP/2 "
                "(http_version=HTTPVersion.HTTP2)"
            )
        self._h2.on_error(
            lambda error_code, error_message, writer: callback(
                _H2Session(writer), error_code, error_message
            )
        )

    async def start(self) -> None:
        """サーバーを起動する"""
        await self._impl().start()

    async def stop(self) -> None:
        """サーバーを停止する"""
        await self._impl().stop()

    async def run(self) -> None:
        """受信ループを実行する

        接続ごとのエラーはコールバック (`on_error` など) で通知し、例外は
        送出しない。
        """
        await self._impl().run()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        await self.stop()

    def _impl(self) -> H3Server | H2Server:
        """選択した HTTP バージョンの実装を返す"""
        impl = self._h3 if self._h3 is not None else self._h2
        if impl is None:
            raise WebTransportError("server implementation is missing")
        return impl
