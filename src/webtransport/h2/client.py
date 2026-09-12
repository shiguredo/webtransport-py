"""WebTransport over HTTP/2 クライアント

asyncio と TCP/TLS を使用した高レベル WebTransport クライアント実装。
Capsule Protocol (RFC 9297) を使用して WebTransport ストリームと DATAGRAM をサポート。
"""

from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING, Literal, Self

from webtransport.exceptions import (
    ConnectRefusedError,
    ConnectTimeoutError,
    HandshakeFailedError,
    WebTransportConnectError,
)
from webtransport.webtransport_ext import h2 as h2_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Client:
    """WebTransport over HTTP/2 クライアント

    asyncio を使用した非同期 WebTransport クライアント。TLS 1.3 以上を必須とし
    (draft-15 Section 7)、TLS 1.2 以下の対向へは接続できない。

    Usage:
        client = Client(url="https://localhost:8443/webtransport")
        await client.connect()
        stream_id = await client.open_stream()
        await client.send_stream_data(stream_id, b"Hello")
        await client.send_datagram(b"Hello DG")
        await client.run()
        await client.close()

        # または
        async with Client(url="https://localhost:8443/webtransport") as client:
            stream_id = await client.open_stream()
            await client.send_stream_data(stream_id, b"Hello")
            await client.run()
    """

    def __init__(
        self,
        url: str,
        verify_peer: bool = True,
        origin: str = "",
        config: h2_low.Config | None = None,
        close_wait_timeout: float = 3.0,
    ) -> None:
        """クライアントを初期化する

        Args:
            url: WebTransport エンドポイント URL
            verify_peer: サーバー証明書を検証するかどうか
            origin: Origin ヘッダー値 (空なら付与しない)
            config: HTTP/2 / WebTransport セッション設定。省略時は既定値。
                呼び出し元のオブジェクトは書き換えない
            close_wait_timeout: close() でピアの CONNECT ストリームクローズを
                待つ上限秒数。0 以下なら待機しない
        """
        self._url = url
        self._host, self._port, self._path = self._parse_url(url)
        self._verify_peer = verify_peer
        self._origin = origin
        self._user_config = config
        self._close_wait_timeout = close_wait_timeout

        self._session: h2_low.Session | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._running = False
        self._connected = False
        self._session_id = -1
        # close() の待機で観測するピアの CONNECT ストリームクローズ記録。
        # run() と close() のどちらが観測しても記録する
        self._peer_closed_session_ids: set[int] = set()
        # 直近の close() の待機結果
        self._close_wait_result: Literal["peer-closed", "timeout", "skipped", "none"] = "none"
        # run() が実際に実行中かどうか。_running は connect() 成功時に立って
        # run() の起動有無に関わらず True のため、受信の同時呼び出し回避には
        # 実行中フラグを使う
        self._run_active = False
        # run() がコールバックを実行中かどうか。実行中は run() が受信して
        # いないため、コールバックから (子タスク経由を含めて) close() が
        # 呼ばれても close() が自身で受信できる
        self._in_callback = False
        # connect() が SESSION_READY を消費したときの引き継ぎバッファ。
        # run() のイベントループ開始時に先に処理し、コールバック登録の
        # 順序に依存せず on_session_ready を発火させる (イベントは
        # キューから取り出した時点で確定するため、1 件のみ保持すればよい)
        self._pending_session_ready: int | None = None

        self._on_session_ready: Callable[[int], Awaitable[None]] | None = None
        self._on_session_closed: Callable[[int], Awaitable[None]] | None = None
        self._on_stream_data: Callable[[int, bytes], Awaitable[None]] | None = None
        self._on_stream_reset: Callable[[int, int], Awaitable[None]] | None = None
        self._on_stop_sending: Callable[[int, int], Awaitable[None]] | None = None
        self._on_datagram: Callable[[bytes], Awaitable[None]] | None = None
        self._on_error: Callable[[int, str], Awaitable[None]] | None = None
        self._on_goaway: Callable[[int, int], Awaitable[None]] | None = None
        self._goaway_notified = False

    @property
    def url(self) -> str:
        """接続先 URL"""
        return self._url

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
        """WebTransport セッションが確立しているかどうか"""
        return self._connected

    @property
    def session_id(self) -> int:
        """セッション ID"""
        return self._session_id

    def on_session_ready(
        self,
        callback: Callable[[int], Awaitable[None]],
    ) -> None:
        """セッション確立時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int) -> None
        """
        self._on_session_ready = callback

    def on_session_closed(
        self,
        callback: Callable[[int], Awaitable[None]],
    ) -> None:
        """セッション終了時のコールバックを設定する

        Args:
            callback: async def callback(session_id: int) -> None
        """
        self._on_session_closed = callback

    def on_stream_data(
        self,
        callback: Callable[[int, bytes], Awaitable[None]],
    ) -> None:
        """ストリームデータ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes) -> None
        """
        self._on_stream_data = callback

    def on_stream_reset(
        self,
        callback: Callable[[int, int], Awaitable[None]],
    ) -> None:
        """ストリームリセット受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, error_code: int) -> None
        """
        self._on_stream_reset = callback

    def on_datagram(
        self,
        callback: Callable[[bytes], Awaitable[None]],
    ) -> None:
        """データグラム受信時のコールバックを設定する

        Args:
            callback: async def callback(data: bytes) -> None
        """
        self._on_datagram = callback

    def on_stop_sending(
        self,
        callback: Callable[[int, int], Awaitable[None]],
    ) -> None:
        """ピアからの WT_STOP_SENDING 受信時のコールバックを設定する

        ピアが送信側の停止を要求したときに、そのアプリケーションエラーコードと
        ともに呼ばれる (draft-ietf-webtrans-http2-15 Section 6.3)。受信側の
        低レベル層は送信側が Ready / Send 状態なら WT_RESET_STREAM を自動で
        返すため、このコールバックは通知のみを担う。

        Args:
            callback: async def callback(stream_id: int, error_code: int) -> None
        """
        self._on_stop_sending = callback

    def on_error(
        self,
        callback: Callable[[int, str], Awaitable[None]],
    ) -> None:
        """受信フロー制御違反のコールバックを設定する

        WT_FLOW_CONTROL_ERROR (error_code 0x50) のみを渡す。0x50 は
        draft-15 Section 3.4 の 0xTBD のプレースホルダであり、draft で値が
        確定したら更新する。WT_STREAM_STATE_ERROR (0x51) や nghttp2 /
        SETTINGS 違反の Error イベントは対象外。セッションエラーは HTTP/2
        接続を終了しない (draft-15 Section 3.4)。

        Args:
            callback: async def callback(error_code: int, error_message: str) -> None
        """
        self._on_error = callback

    def on_goaway(
        self,
        callback: Callable[[int, int], Awaitable[None]],
    ) -> None:
        """GOAWAY 受信時のコールバックを設定する

        graceful shutdown の通知であり、既存セッションの送受信は継続する
        (draft-15 Section 6.13)。初回受信のみ発火する。

        Args:
            callback: async def callback(last_stream_id: int, error_code: int) -> None
        """
        self._on_goaway = callback

    def _parse_url(self, url: str) -> tuple[str, int, str]:
        """URL をパースする"""
        url = url.replace("https://", "")
        if "/" in url:
            host_port, path = url.split("/", 1)
            path = "/" + path
        else:
            host_port = url
            path = "/"

        if ":" in host_port:
            host, port_str = host_port.split(":")
            port = int(port_str)
        else:
            host = host_port
            port = 443

        return host, port, path

    async def _send_pending(self) -> None:
        """送信待ちデータを送信する"""
        if self._session is None or self._writer is None:
            return

        data = self._session.send()
        if data:
            self._writer.write(data)
            await self._writer.drain()

    async def _receive(self, timeout: float = 0.1) -> None:
        """データを受信する"""
        if self._session is None or self._reader is None:
            return

        try:
            data = await asyncio.wait_for(self._reader.read(65535), timeout=timeout)
            if data:
                self._session.receive(data)
            else:
                self._running = False
        except TimeoutError:
            pass

    async def _wait_webtransport_ready(self, timeout_seconds: float = 5.0) -> bool:
        """対向の SETTINGS を待ち WebTransport が利用可能になるまで待機する

        draft-15 Section 3.1: ENABLE_CONNECT_PROTOCOL と WT_ENABLED を
        受信するまで CONNECT してはならない。
        """
        if self._session is None:
            return False

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if self._session.is_webtransport_ready():
                return True
            await self._receive()
            await self._send_pending()
            await asyncio.sleep(0.001)
        return False

    async def connect(self, timeout: float = 10.0) -> None:
        """WebTransport セッションを確立する

        deadline ベースで bounded に動作する。成功時は例外なしで復帰し、
        失敗時は具体例外で理由を通知する。TLS 1.3 以上を必須とし
        (draft-15 Section 7)、TLS 1.2 以下の対向へは接続できない。仕様上
        許容される TLS 1.2 + extended master secret (EMS) も、Python の ssl
        が EMS 交渉の有無を公開しないため拒否する。

        Args:
            timeout: 接続確立の打ち切り秒数。TCP 接続・SETTINGS 受信待ち・
                2xx 応答待ちの全待機が同一 deadline を参照する。0 以下では
                即座に ConnectTimeoutError を送出する

        Raises:
            ConnectTimeoutError: 待機中に成否を決めるイベントが届かず
                deadline に達した場合
            ConnectRefusedError: 待機中に接続リセット (TCP RST) が届いた
                場合 (TLS バージョン不一致が接続リセットとして観測される
                環境を含む)
            HandshakeFailedError: TLS 検証失敗、TLS アラート (TLS バージョン
                不一致や ALPN 不一致) の受信、非 2xx 応答の場合
            ValueError: Config の上限値 (2^32 - 1) を超えるためセッション
                生成に失敗した場合
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout

        # 再接続で前回のピアクローズ観測を引き継がない (CONNECT ストリーム ID は
        # 接続ごとに再利用され得るため)
        self._peer_closed_session_ids.clear()
        self._close_wait_result = "none"

        if self._verify_peer:
            ssl_context = ssl.create_default_context()
        else:
            ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
        # draft-15 Section 7: TLS 1.3 以上と TLS 1.2 + extended master secret
        # (EMS) のいずれも満たさない接続では WebTransport over HTTP/2
        # リクエストを送信してはならない (MUST NOT)。Python の ssl は EMS
        # 交渉の有無を公開しないため、TLS 1.3 以上を許可し TLS 1.2 以下を
        # 拒否する (draft の改版で要件が変わる可能性がある)
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_3
        ssl_context.set_alpn_protocols(["h2"])

        try:
            remaining = deadline - loop.time()
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(
                    self._host,
                    self._port,
                    ssl=ssl_context,
                ),
                timeout=max(0.0, remaining),
            )
        except ssl.SSLError as exc:
            # builtin の ConnectionRefusedError と自前の ConnectRefusedError
            # (綴りが 3 文字違い) を取り違えないこと。前者は OSError 派生の
            # 標準例外で __cause__ に保持し、後者を送出する
            raise HandshakeFailedError(f"TLS handshake failed: {exc}") from exc
        except TimeoutError as exc:
            raise ConnectTimeoutError(
                f"TCP connection did not complete within {timeout} seconds"
            ) from exc
        except ConnectionRefusedError as exc:
            raise ConnectRefusedError(f"connection refused: {exc}") from exc
        except OSError as exc:
            raise ConnectRefusedError(f"connection failed before TLS handshake: {exc}") from exc

        try:
            # H2Session は Config を値コピーする。呼び出し元のオブジェクトは
            # 書き換えない。役割 (クライアント) は create_client が決める
            config = self._user_config if self._user_config is not None else h2_low.Config()
            try:
                self._session = h2_low.Session.create_client(config)
            except ValueError:
                # Config の上限検査エラーなど生成時の入力検証失敗の後始末
                # (利用者入力の誤用)。接続と状態を残さない
                self._running = False
                self._connected = False
                if self._writer is not None:
                    self._writer.close()
                self._reader = None
                self._writer = None
                raise
            # 新規接続のため GOAWAY 通知済み印を戻す
            self._goaway_notified = False

            await self._send_pending()

            self._running = True

            # draft-15 Section 3.1: SETTINGS 受信後に Extended CONNECT を送る
            remaining = deadline - loop.time()
            if remaining <= 0 or not await self._wait_webtransport_ready(timeout_seconds=remaining):
                if not self._running:
                    # _receive が EOF を検知して停止した場合は接続喪失として
                    # 拒否に寄せる (2xx 待ちの EOF 扱いと同型)
                    self._connected = False
                    raise ConnectRefusedError("connection lost while waiting for SETTINGS")
                self._running = False
                raise ConnectTimeoutError(f"HTTP/2 SETTINGS not received within {timeout} seconds")

            self._session_id = self._session.connect(self._url, self._origin)
            if self._session_id < 0:
                self._running = False
                raise HandshakeFailedError("failed to send Extended CONNECT request")

            await self._send_pending()

            # 2xx レスポンス (200 OK 等) を待つ
            while self._running and loop.time() < deadline:
                await self._receive()
                await self._send_pending()

                while True:
                    event = self._session.next_event()
                    if event is None:
                        break

                    if (
                        event.type == h2_low.EventType.SESSION_READY
                        and event.session_id == self._session_id
                    ):
                        self._connected = True
                        # run() のイベントループで on_session_ready を発火させる
                        # ため、イベントを未配信バッファへ引き継ぐ
                        # (コールバック登録の順序に依存しないため)
                        self._pending_session_ready = event.session_id
                        return

                    if (
                        event.type == h2_low.EventType.SESSION_CLOSED
                        and event.session_id == self._session_id
                    ):
                        self._connected = False
                        self._running = False
                        raise HandshakeFailedError("session closed before 2xx response")

                    # 非 2xx 拒否 (draft-15 Section 3.2: 2xx 以外はセッション未確立)。
                    # SESSION_READY / SESSION_CLOSED のどちらも発火しないため、
                    # 待たずに HandshakeFailedError を送出して終了する。
                    # bindings は 2xx 全般 (先頭文字が '2') を確立とみなすため、
                    # 2xx 非 200 (201 等) でも SESSION_READY が発火する
                    if (
                        event.type == h2_low.EventType.SESSION_REJECTED
                        and event.session_id == self._session_id
                    ):
                        self._connected = False
                        self._running = False
                        raise HandshakeFailedError("server rejected session with non-2xx response")

                if not self._running:
                    self._connected = False
                    raise ConnectRefusedError("connection lost while waiting for 2xx response")

                await asyncio.sleep(0.001)

            self._connected = False
            self._running = False
            raise ConnectTimeoutError(f"2xx response not received within {timeout} seconds")

        except TimeoutError as exc:
            # 将来 try 内に wait_for が追加される場合に備え、漏れた
            # TimeoutError は deadline 到達として明示的に受ける
            self._running = False
            self._connected = False
            if self._writer is not None:
                self._writer.close()
                self._writer = None
            raise ConnectTimeoutError(
                f"connection attempt did not complete within {timeout} seconds"
            ) from exc
        except (OSError, WebTransportConnectError) as exc:
            # 確立中の素の OSError (drain 時の RST 等) は ConnectRefusedError
            # に寄せて具体例外の契約を保つ。失敗パスの後始末は best-effort
            # であり、完全な切断 (close_session 送出等) は呼び出し側の
            # close() が担う
            self._running = False
            self._connected = False
            if self._writer is not None:
                self._writer.close()
                self._writer = None
            if isinstance(exc, WebTransportConnectError):
                raise
            raise ConnectRefusedError(f"connection failed during establishment: {exc}") from exc

    async def open_stream(self, unidirectional: bool = False) -> int:
        """WebTransport ストリームを開く

        Args:
            unidirectional: 単方向ストリームにするかどうか

        Returns:
            ストリーム ID
        """
        if self._session is None or self._session_id < 0:
            return -1

        stream_id = self._session.open_stream(self._session_id, unidirectional)
        await self._send_pending()
        return stream_id

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

        Raises:
            ValueError: 接続済みで data が 1 MiB 超の場合
        """
        if self._session is None or self._session_id < 0:
            return

        self._session.send_stream_data(self._session_id, stream_id, data, fin)
        await self._send_pending()

    async def send_datagram(self, data: bytes) -> None:
        """データグラムを送信する

        Capsule Protocol の DATAGRAM capsule (RFC 9297) を使う。
        draft-15 Section 6.11

        終了したセッション ID (WT_CLOSE_SESSION 受信後 / close_session 後 /
        ピアの END_STREAM 受信後) への送信は無視される (draft-15 Section 3.4
        のセッション終了。仕様強制ではなく実装ポリシー)。

        Args:
            data: 送信データ

        Raises:
            ValueError: 接続済みで data が 1 MiB 超の場合
        """
        if self._session is None or self._session_id < 0:
            return

        self._session.send_datagram(self._session_id, data)
        await self._send_pending()

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセットする

        Args:
            stream_id: ストリーム ID
            error_code: エラーコード

        Raises:
            ValueError: stream_id が 2^62 以上の場合 (varint の上限)。
                存在しないストリーム ID へは送出せず無視する。
        """
        if self._session is None or self._session_id < 0:
            return

        self._session.reset_stream(self._session_id, stream_id, error_code)
        await self._send_pending()

    async def stop_sending(self, stream_id: int, error_code: int = 0) -> None:
        """ピアに送信停止を要求する (WT_STOP_SENDING)

        受信側の停止を要求し、送信側が Ready / Send 状態ならピアが
        WT_RESET_STREAM を返す (draft-ietf-webtrans-http2-15 Section 6.3 /
        RFC 9000 Section 3.5)。

        Args:
            stream_id: ストリーム ID
            error_code: アプリケーションエラーコード

        Raises:
            ValueError: stream_id が 2^62 以上の場合 (varint の上限)。
                存在しないストリーム ID へは送出せず無視する。
        """
        if self._session is None or self._session_id < 0:
            return

        self._session.stop_sending(self._session_id, stream_id, error_code)
        await self._send_pending()

    async def _invoke_callback(
        self, callback: Callable[..., Awaitable[None]], *args: object
    ) -> None:
        """run() のイベントコールバックを実行する

        実行中は run() が受信していないことを _in_callback で示し、コールバック
        から (子タスク経由を含めて) close() が呼ばれた場合に close() 側が自身で
        受信できるようにする。
        """
        self._in_callback = True
        try:
            await callback(*args)
        finally:
            self._in_callback = False

    async def run(self) -> None:
        """メインループを実行する

        接続が終了するまでブロックする。
        """
        if self._session is None:
            raise RuntimeError("client is not connected")

        self._run_active = True
        try:
            # connect() が消費した SESSION_READY を引き継ぐ (コールバック登録の
            # 順序に依存せず、run() のイベントループで発火させる)
            if self._pending_session_ready is not None:
                pending_session_id = self._pending_session_ready
                self._pending_session_ready = None
                if self._on_session_ready is not None:
                    await self._invoke_callback(self._on_session_ready, pending_session_id)

            while self._running:
                await self._receive()
                await self._send_pending()

                while True:
                    event = self._session.next_event()
                    if event is None:
                        break

                    if (
                        event.type == h2_low.EventType.SESSION_READY
                        and self._on_session_ready is not None
                    ):
                        await self._invoke_callback(self._on_session_ready, event.session_id)

                    elif event.type == h2_low.EventType.SESSION_CLOSED:
                        # close() の待機でも参照するため、コールバックとは別に記録する
                        self._peer_closed_session_ids.add(event.session_id)
                        self._connected = False
                        if self._on_session_closed is not None:
                            await self._invoke_callback(self._on_session_closed, event.session_id)

                    elif (
                        event.type == h2_low.EventType.STREAM_DATA
                        and self._on_stream_data is not None
                    ):
                        await self._invoke_callback(
                            self._on_stream_data, event.stream_id, event.data
                        )

                    elif (
                        event.type == h2_low.EventType.STREAM_RESET
                        and self._on_stream_reset is not None
                    ):
                        await self._invoke_callback(
                            self._on_stream_reset, event.stream_id, event.error_code
                        )

                    elif (
                        event.type == h2_low.EventType.STOP_SENDING
                        and self._on_stop_sending is not None
                    ):
                        await self._invoke_callback(
                            self._on_stop_sending, event.stream_id, event.error_code
                        )

                    elif event.type == h2_low.EventType.DATAGRAM and self._on_datagram is not None:
                        await self._invoke_callback(self._on_datagram, event.data)

                    elif event.type == h2_low.EventType.GOAWAY and not self._goaway_notified:
                        self._goaway_notified = True
                        if self._on_goaway is not None:
                            await self._invoke_callback(
                                self._on_goaway, event.last_stream_id, event.error_code
                            )

                    # 0x50 (WT_FLOW_CONTROL_ERROR) のみ on_error へ渡す
                    elif (
                        event.type == h2_low.EventType.ERROR
                        and event.error_code == 0x50
                        and self._on_error is not None
                    ):
                        await self._invoke_callback(
                            self._on_error, event.error_code, event.error_message
                        )

                if self._session.is_closed():
                    self._running = False

                await asyncio.sleep(0.01)
        finally:
            self._run_active = False

    async def close(self) -> None:
        """接続を閉じる

        draft-15 Section 6.12: WT_CLOSE_SESSION 後に CONNECT ストリームを
        half-close し、ピアの CONNECT ストリームクローズ (END_STREAM /
        RST_STREAM) を close_wait_timeout の上限まで待ってから TCP/TLS を
        閉じる。上限で打ち切った場合も閉じる処理へ進む。
        """
        self._running = False
        self._connected = False
        # 未配信の SESSION_READY を破棄する (再 connect() の際に古い
        # セッション ID で発火させないため)
        self._pending_session_ready = None

        try:
            if self._session is not None and self._session_id >= 0:
                session_id = self._session_id
                self._session.close_session(session_id)
                try:
                    await self._send_pending()
                except OSError:
                    # 接続断 (RST 等) では送出できなくても閉じる処理へ進む
                    pass
                # 二重 close() で待機を繰り返さないようセッション ID を破棄する
                self._session_id = -1
                if self._close_wait_timeout <= 0:
                    self._close_wait_result = "skipped"
                elif await self._wait_for_peer_close(session_id):
                    self._close_wait_result = "peer-closed"
                else:
                    self._close_wait_result = "timeout"
        finally:
            if self._writer is not None:
                self._writer.close()
                try:
                    await self._writer.wait_closed()
                except ssl.SSLError, ConnectionError, OSError:
                    pass

    async def _wait_for_peer_close(self, session_id: int) -> bool:
        """ピアの CONNECT ストリームクローズを待つ。観測したら True を返す

        draft-15 Section 6.12 の MUST (WT_CLOSE_SESSION の受信側は END_STREAM
        で応答してストリームを閉じる) への応答を best-effort で観測する
        (draft の改版で要件が変わる可能性がある)。終了条件はピアクローズの
        観測・接続断 (EOF / RST 等)・close_wait_timeout の満了で、いずれも
        呼び出し側は閉じる処理へ進む。
        """
        if self._session is None:
            return False
        if session_id in self._peer_closed_session_ids:
            return True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._close_wait_timeout
        # run() が実行中でも、コールバック実行中は run() が受信していないため
        # 自身で受信する (子タスク経由の close() もここで拾う)
        from_run_callback = self._in_callback
        while self._run_active and not from_run_callback and loop.time() < deadline:
            if session_id in self._peer_closed_session_ids:
                return True
            await asyncio.sleep(0.001)
        while loop.time() < deadline:
            if session_id in self._peer_closed_session_ids:
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                await self._receive(timeout=min(0.1, remaining))
                await self._send_pending()
            except OSError:
                # 接続断 (RST 等) では待機を打ち切って閉じる処理へ進む
                break
            # 待機中はイベントコールバックを発火せず、SESSION_CLOSED の記録
            # だけを行う (close() 前の従来動作を保ち、run() との再入を避ける)
            while True:
                event = self._session.next_event()
                if event is None:
                    break
                if event.type == h2_low.EventType.SESSION_CLOSED:
                    self._peer_closed_session_ids.add(event.session_id)
            if self._reader is None or self._reader.at_eof():
                # ピアが接続自体を閉じた (EOF)
                break
            await asyncio.sleep(0.001)
        return session_id in self._peer_closed_session_ids

    async def __aenter__(self) -> Self:
        """非同期コンテキストマネージャーのエントリーポイント"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """非同期コンテキストマネージャーの終了処理"""
        await self.close()
