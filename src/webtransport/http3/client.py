"""HTTP/3 クライアント

asyncio と UDP を使用した高レベル HTTP/3 クライアント実装。
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING, Self

from webtransport.exceptions import (
    ConnectRefusedError,
    ConnectTimeoutError,
    HandshakeFailedError,
    WebTransportConnectError,
)
from webtransport.http3.constants import H3_GENERAL_PROTOCOL_ERROR
from webtransport.webtransport_ext import http3 as http3_low
from webtransport.webtransport_ext import quic as quic_low

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Client:
    """HTTP/3 クライアント

    asyncio を使用した非同期 HTTP/3 クライアント。

    Usage:
        client = Client(host="example.com", port=443)
        await client.connect()
        stream_id = await client.request("GET", "/")
        await client.run()
        await client.close()

        # または
        async with Client(host="example.com", port=443) as client:
            stream_id = await client.request("GET", "/")
            await client.run()
    """

    def __init__(
        self,
        host: str,
        port: int = 443,
        idle_timeout_ns: int = 30_000_000_000,
        verify_peer: bool = True,
        ca_file: str | None = None,
        verify_callback: Callable[[list[bytes]], bool] | None = None,
    ) -> None:
        """クライアントを初期化する

        Args:
            host: 接続先ホスト
            port: 接続先ポート
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
            verify_peer: サーバー証明書を検証するかどうか
            ca_file: CA 証明書ファイルパス
            verify_callback: ピア証明書検証コールバック
        """
        self._host = host
        self._port = port
        self._idle_timeout_ns = idle_timeout_ns
        self._verify_peer = verify_peer
        self._ca_file = ca_file
        self._verify_callback = verify_callback

        self._quic_connection: quic_low.Connection | None = None
        self._http3_connection: http3_low.Connection | None = None
        self._socket: socket.socket | None = None
        # bind 後のローカルアドレス (host, port)
        self._local_addr: tuple[str, int] | None = None
        # 接続中のリモートアドレス (数値 IP。試行ごとに設定する)
        self._remote_addr: tuple[str, int] | None = None
        self._running = False
        self._connected = False
        self._control_stream_id = -1

        self._on_headers: Callable[[int, list[tuple[str, str]]], Awaitable[None]] | None = None
        self._on_data: Callable[[int, bytes], Awaitable[None]] | None = None
        self._on_stream_end: Callable[[int], Awaitable[None]] | None = None
        self._on_connection_error: Callable[[int, str], Awaitable[None]] | None = None
        # 直前の Error イベントで得た H3 ワイヤーエラーコードとメッセージ。
        # _close_on_h3_error が QUIC CONNECTION_CLOSE に載せる
        self._h3_error_code: int = H3_GENERAL_PROTOCOL_ERROR
        self._h3_error_message: str = "http3 protocol error"
        self._on_stream_reset: Callable[[int, int], Awaitable[None]] | None = None

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

    def on_headers(
        self,
        callback: Callable[[int, list[tuple[str, str]]], Awaitable[None]],
    ) -> None:
        """ヘッダー受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, headers: list[tuple[str, str]]) -> None
        """
        self._on_headers = callback

    def on_data(
        self,
        callback: Callable[[int, bytes], Awaitable[None]],
    ) -> None:
        """データ受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, data: bytes) -> None
        """
        self._on_data = callback

    def on_connection_error(
        self,
        callback: Callable[[int, str], Awaitable[None]],
    ) -> None:
        """HTTP/3 プロトコルエラー検知時のコールバックを設定する

        低レベルが nghttp3 の負値 return で自主クローズしたときに、RFC 9114
        Section 8.1 の H3 ワイヤーエラーコードとメッセージが渡される。接続は
        この後 QUIC CONNECTION_CLOSE で閉じられる。

        Args:
            callback: async def callback(error_code: int, error_message: str) -> None
        """
        self._on_connection_error = callback

    def on_stream_end(
        self,
        callback: Callable[[int], Awaitable[None]],
    ) -> None:
        """ストリーム終了時のコールバックを設定する

        クライアントが受信したレスポンスストリームの QUIC FIN (ストリーム
        終端) を検知したときに呼ばれる。RESET / キャンセルで終了した場合
        は呼ばれず、on_stream_reset が担う。

        Args:
            callback: async def callback(stream_id: int) -> None
        """
        self._on_stream_end = callback

    def on_stream_reset(
        self,
        callback: Callable[[int, int], Awaitable[None]],
    ) -> None:
        """ストリームリセット受信時のコールバックを設定する

        Args:
            callback: async def callback(stream_id: int, error_code: int) -> None
        """
        self._on_stream_reset = callback

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
        """パケットの送信先アドレスを決める"""
        if packet.remote_host and packet.remote_port:
            return (packet.remote_host, packet.remote_port)
        # 数値リモートがあればそれを使い、なければホスト名にフォールバック
        # する (C++ 側で解決されるが family 食い違いの余地が残る)
        if self._remote_addr is not None:
            return self._remote_addr
        return (self._host, self._port)

    async def _send_pending(self) -> int:
        """送信待ちデータを送出できるだけ送出する

        send() は輻輳ウィンドウの枯渇・フロー制御・送信待ちの解消のいずれかで
        必ず None を返すため、None まで drain しても戻ってこなくならない。

        Returns:
            送信したパケット数
        """
        if self._quic_connection is None or self._http3_connection is None:
            return 0
        if self._socket is None:
            return 0

        for stream_id, stream_data, fin in self._http3_connection.get_streams_to_send():
            self._quic_connection.send_stream_data(stream_id, stream_data, fin)

        loop = asyncio.get_running_loop()
        sent = 0
        while True:
            packet = self._quic_connection.send()
            if packet is None:
                return sent
            await loop.sock_sendto(
                self._socket,
                packet.data,
                self._destination_for_packet(packet),
            )
            sent += 1

    async def _drain_all(self) -> None:
        """QUIC の送信キューにあるパケットをすべて送出する

        close() 後は draining 状態に入って新規データ生成が止まるため、
        _send_pending の 1 パケット制約 (ACK 待ちでハングする懸念) は該当しない。
        CONNECTION_CLOSE を含む残存パケットを確実にピアへ送出するために使用する。
        低レベル QUIC 側の実装バグで send() が延々とパケットを返し続けても
        run() 全体が凍らないよう、防御的に上限を設ける。
        """
        if self._quic_connection is None or self._socket is None:
            return

        loop = asyncio.get_running_loop()
        # 通常は 1〜数パケットで返り値が None になる。64 は防御的な上限
        for _ in range(64):
            packet = self._quic_connection.send()
            if packet is None:
                return
            await loop.sock_sendto(
                self._socket,
                packet.data,
                self._destination_for_packet(packet),
            )

    async def _close_on_h3_error(self) -> None:
        """HTTP/3 プロトコルエラー検知時に QUIC を閉じて run() を終了する

        RFC 9114 Section 5.3 (Immediate Application Closure) に沿って
        QUIC CONNECTION_CLOSE を送出したうえで _running を落とす。
        error_code は直前の Error イベントで得た RFC 9114 Section 8.1 の
        H3 ワイヤーコードを使う。Error イベントを経ずに閉じた場合
        (テスト専用の強制クローズ等) は H3_GENERAL_PROTOCOL_ERROR を使う。
        """
        if self._quic_connection is not None and not self._quic_connection.is_closed():
            self._quic_connection.close(self._h3_error_code, self._h3_error_message)
            await self._drain_all()
        self._running = False
        self._connected = False

    async def _receive(self) -> None:
        """データを受信する"""
        if self._quic_connection is None or self._socket is None:
            return
        if self._local_addr is None:
            return

        loop = asyncio.get_running_loop()
        try:
            data, raw_remote = await asyncio.wait_for(
                loop.sock_recvfrom(self._socket, 65535),
                timeout=0.1,
            )
        except TimeoutError:
            return

        remote = self._normalize_addr(raw_remote)
        self._quic_connection.receive(data, self._local_addr, remote)

    def _setup_http3_streams(self) -> None:
        """HTTP/3 制御ストリームを設定する"""
        if self._quic_connection is None or self._http3_connection is None:
            return

        if self._control_stream_id < 0:
            self._control_stream_id = self._quic_connection.open_stream(False)
            self._http3_connection.bind_control_stream(self._control_stream_id)

            encoder_stream_id = self._quic_connection.open_stream(False)
            self._http3_connection.bind_qpack_encoder_stream(encoder_stream_id)

            decoder_stream_id = self._quic_connection.open_stream(False)
            self._http3_connection.bind_qpack_decoder_stream(decoder_stream_id)

    async def _resolve_remote(self, host: str, port: int) -> list[tuple[socket.AddressFamily, str]]:
        """ホストを解決して (family, 数値 IP) の候補列を返す

        getaddrinfo の順序を保ち、重複を除く。TLS の server_name には元の
        ホスト名を使い続けるため、ここでは数値 IP のみを取り出す。
        """
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
        candidates: list[tuple[socket.AddressFamily, str]] = []
        seen: set[tuple[socket.AddressFamily, str]] = set()
        for family, _, _, _, sockaddr in infos:
            ip = sockaddr[0]
            if not isinstance(ip, str):
                continue
            candidate = (family, ip)
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
        return candidates

    async def connect(self, timeout: float = 10.0) -> None:
        """サーバーに接続する

        候補ごとに試行し、各試行は deadline ベースで bounded に動作する。
        成功時は例外なしで復帰し、失敗時は具体例外で理由を通知する
        (h3 / h2 対称の例外送出型)。

        Args:
            timeout: 各候補への試行の打ち切り秒数。候補ごとに適用される
                ため、合計は候補数倍になり得る。0 以下では即座に
                ConnectTimeoutError を送出する

        Raises:
            ConnectTimeoutError: 待機中にハンドシェイク完了イベントが届かず
                deadline に達した場合
            ConnectRefusedError: 生成失敗時・確立中の OSError 時など、前段
                での接続拒否の場合
            HandshakeFailedError: 待機中に QUIC 側の明示的な
                `CONNECTION_CLOSE` が届いた場合 (ハンドシェイク完了前の
                失敗は TLS 由来とみなす)
        """
        if timeout <= 0:
            raise ConnectTimeoutError(f"QUIC handshake did not complete within {timeout} seconds")
        quic_config = quic_low.Config()
        quic_config.alpn_protocols = ["h3"]
        quic_config.idle_timeout_ns = self._idle_timeout_ns
        quic_config.verify_peer = self._verify_peer
        quic_config.server_name = self._host
        if self._ca_file is not None:
            quic_config.ca_file = self._ca_file
        if self._verify_callback is not None:
            quic_config.verify_callback = self._verify_callback

        http3_config = http3_low.Config()
        http3_config.is_server = False

        # 名前解決は Python 側で非同期に行い、family 順の候補列を作る。
        # C++ 側には数値 IP を渡し、ソケット family との食い違いを避ける
        try:
            candidates = await self._resolve_remote(self._host, self._port)
        except OSError as exc:
            raise ConnectRefusedError(
                f"failed to resolve {self._host}:{self._port}: {exc}"
            ) from exc
        if not candidates:
            raise ConnectTimeoutError(f"QUIC handshake did not complete within {timeout} seconds")

        # 候補ごとに試す (逐次フォールバック)。各試行は timeout 全体で
        # 駆動する (先頭候補の無応答で予算を使い切ると次候補へ進めない
        # ため、予算は分割しない)。応答なしのタイムアウト失敗時のみ次候補
        # へ進み、明示失敗は即座に送出する
        loop = asyncio.get_running_loop()
        last_error: ConnectTimeoutError | None = None
        for family, ip in candidates:
            try:
                await self._connect_one(
                    family, ip, quic_config, http3_config, timeout, loop.time() + timeout
                )
                return
            except ConnectTimeoutError as exc:
                last_error = exc
                await self._abandon_attempt()
                continue
        if last_error is not None:
            raise last_error
        raise ConnectTimeoutError(f"QUIC handshake did not complete within {timeout} seconds")

    async def _abandon_attempt(self) -> None:
        """失敗試行の後始末をして次候補に備える

        ソケットを閉じて参照を破棄する。制御ストリーム ID も戻し、次試行
        の _setup_http3_streams が再設定するようにする。
        """
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._quic_connection = None
        self._http3_connection = None
        self._local_addr = None
        self._remote_addr = None
        self._control_stream_id = -1
        self._running = False
        self._connected = False

    async def _connect_one(
        self,
        family: socket.AddressFamily,
        ip: str,
        quic_config: quic_low.Config,
        http3_config: http3_low.Config,
        timeout: float,
        deadline: float,
    ) -> None:
        """単一候補への接続を試みる。失敗時は具体例外を送出する

        Args:
            family: ソケット family
            ip: 接続先の数値 IP (TLS 検証には元のホスト名を使う)
            quic_config: QUIC 設定 (family 非依存のため呼び出し側で生成)
            http3_config: HTTP/3 設定 (同上)
            timeout: 打ち切り秒数 (メッセージ用)
            deadline: 打ち切り時刻
        """
        loop = asyncio.get_running_loop()
        try:
            self._socket = socket.socket(family, socket.SOCK_DGRAM)
            self._socket.setblocking(False)
            self._socket.bind(("::", 0) if family == socket.AF_INET6 else ("0.0.0.0", 0))
            self._local_addr = self._normalize_addr(self._socket.getsockname())
            self._remote_addr = (ip, self._port)

            try:
                self._quic_connection = quic_low.Connection.create_client(
                    quic_config,
                    self._local_addr,
                    (ip, self._port),
                )
                self._http3_connection = http3_low.Connection.create_client(http3_config)
            except RuntimeError as exc:
                # 生成自体の失敗は接続拒否に寄せる (アドレス解決失敗などの
                # TLS ハンドシェイク前段での接続拒否。内部生成 Config は
                # 固定値のため、実質的にはアドレス起因である)
                raise ConnectRefusedError(
                    f"failed to create QUIC client connection: {exc}"
                ) from exc

            await self._send_pending()
            self._running = True

            while self._running and loop.time() < deadline:
                await self._receive()

                while True:
                    quic_event = self._quic_connection.next_event()
                    if quic_event is None:
                        break

                    if quic_event.type == quic_low.EventType.HANDSHAKE_COMPLETED:
                        self._connected = True
                        self._setup_http3_streams()
                        await self._send_pending()
                        return

                    elif quic_event.type == quic_low.EventType.CONNECTION_CLOSED:
                        self._running = False
                        raise HandshakeFailedError("QUIC handshake failed before completion")

                await self._send_pending()
                # 損失検出タイマーを駆動する (h3 の connect と同形)。送受信だけでは
                # 再送が起きず、1 パケットのロスで確立が止まる
                quic_timeout = self._quic_connection.get_timeout()
                if quic_timeout is not None and quic_timeout <= 0:
                    self._quic_connection.handle_timeout()
                await asyncio.sleep(0.01)

            self._running = False
            raise ConnectTimeoutError(f"QUIC handshake did not complete within {timeout} seconds")
        except TimeoutError as exc:
            # 現状 try 内で wait_for を使うのは _receive のみであり、そこは
            # 吸収済みである。将来の経路追加に備え、漏れた TimeoutError は
            # deadline 到達として明示的に受ける (h3 と同形)
            self._running = False
            self._connected = False
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            raise ConnectTimeoutError(
                f"QUIC handshake did not complete within {timeout} seconds"
            ) from exc
        except (OSError, WebTransportConnectError) as exc:
            # 確立中の素の OSError (DNS 失敗・sendto 失敗等) は
            # ConnectRefusedError に寄せて具体例外の契約を保つ。参照の破棄
            # は _abandon_attempt と同形に行い、完全な切断は呼び出し側の
            # close() が担う
            await self._abandon_attempt()
            if isinstance(exc, WebTransportConnectError):
                raise
            raise ConnectRefusedError(f"connection failed during establishment: {exc}") from exc

    async def request(
        self,
        method: str,
        path: str,
        headers: list[tuple[str, str]] | None = None,
    ) -> int:
        """HTTP リクエストを送信する

        Args:
            method: HTTP メソッド
            path: リクエストパス
            headers: 追加のヘッダー

        Returns:
            ストリーム ID
        """
        if self._quic_connection is None or self._http3_connection is None:
            return -1

        self._setup_http3_streams()

        stream_id = self._quic_connection.open_stream(True)

        request_headers: list[tuple[str, str]] = [
            (":method", method),
            (":path", path),
            (":scheme", "https"),
            (":authority", self._host),
        ]
        if headers is not None:
            request_headers.extend(headers)

        self._http3_connection.submit_request(stream_id, request_headers)
        await self._send_pending()
        return stream_id

    async def send_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """ストリームにデータを送信する

        Args:
            stream_id: ストリーム ID
            data: 送信データ
            fin: ストリームを終了するか
        """
        if self._http3_connection is None:
            return

        self._http3_connection.send_data(stream_id, data, fin)
        await self._send_pending()

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセットする (QUIC RESET_STREAM + nghttp3 通知)

        Args:
            stream_id: ストリーム ID
            error_code: エラーコード
        """
        if self._quic_connection is not None:
            self._quic_connection.reset_stream(stream_id, error_code)
        if self._http3_connection is not None:
            self._http3_connection.reset_stream(stream_id, error_code)
        await self._send_pending()

    async def run(self) -> None:
        """メインループを実行する

        接続が終了するまでブロックする。
        """
        if self._quic_connection is None or self._http3_connection is None:
            raise RuntimeError("client is not connected")

        while self._running:
            await self._receive()

            # 受信 FIN が立った双方向ストリーム (STREAM_END 通知用)
            finished_streams: list[int] = []

            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break

                if quic_event.type == quic_low.EventType.STREAM_DATA:
                    self._http3_connection.receive_stream_data(
                        quic_event.stream_id,
                        quic_event.data,
                        quic_event.fin,
                    )
                    # nghttp3_conn_close_stream は大きな応答受信中に
                    # 残りの DATA イベントを落とすことがあるため使わない。
                    # QUIC の FIN をストリーム終端の合図として使う。
                    if quic_event.fin and quic_event.stream_id % 4 in (0, 1):
                        finished_streams.append(quic_event.stream_id)
                elif quic_event.type == quic_low.EventType.STREAM_RESET:
                    if self._on_stream_reset is not None:
                        await self._on_stream_reset(
                            quic_event.stream_id,
                            quic_event.error_code,
                        )
                elif quic_event.type == quic_low.EventType.CONNECTION_CLOSED:
                    self._running = False
                    self._connected = False

            while True:
                http3_event = self._http3_connection.next_event()
                if http3_event is None:
                    break

                if http3_event.type == http3_low.EventType.HEADERS:
                    if self._on_headers is not None:
                        await self._on_headers(http3_event.stream_id, http3_event.headers)

                elif http3_event.type == http3_low.EventType.DATA:
                    if self._on_data is not None:
                        await self._on_data(http3_event.stream_id, http3_event.data)

                # STREAM_END イベント (ヘッダー終端などで低レベルが発火する
                # 終端検知) はここでは消費しない: on_stream_end は受信した
                # QUIC FIN (finished_streams) の単一経路で通知する。ヘッダー
                # と FIN が同一の QUIC STREAM_DATA として届くと両経路で
                # 通知され 2 回呼ばれるため (RFC 9114 Section 4.1 の
                # メッセージフレーミングと Section 6 のフレーム境界と
                # QUIC STREAM_DATA 境界の独立性により、1 チャンクで
                # 届くのは正当なワイヤパターン。実ブラウザ等が送り得る)。
                # 低レベルの STREAM_END イベント (ヘッダー終端の終端検知) は
                # 低レベル API の契約としてそのまま維持される

                elif http3_event.type == http3_low.EventType.ERROR:
                    # HTTP/3 プロトコルエラー。QUIC CONNECTION_CLOSE に載せる
                    # error_code を後段の _close_on_h3_error で使うため記録する
                    self._h3_error_code = http3_event.error_code
                    self._h3_error_message = http3_event.error_message
                    if self._on_connection_error is not None:
                        await self._on_connection_error(
                            http3_event.error_code,
                            http3_event.error_message,
                        )

                elif http3_event.type == http3_low.EventType.RESET_STREAM:
                    self._quic_connection.reset_stream(
                        http3_event.stream_id,
                        http3_event.error_code,
                    )

                elif http3_event.type == http3_low.EventType.STOP_SENDING:
                    self._quic_connection.stop_sending(
                        http3_event.stream_id,
                        http3_event.error_code,
                    )

            # HTTP/3 の DATA を処理したあとに STREAM_END を通知する
            if self._on_stream_end is not None:
                for stream_id in finished_streams:
                    await self._on_stream_end(stream_id)

            await self._send_pending()

            timeout = self._quic_connection.get_timeout()
            if timeout is not None and timeout <= 0:
                self._quic_connection.handle_timeout()

            # HTTP/3 層のプロトコルエラーで低レベルが自主クローズしたとき、
            # QUIC の CONNECTION_CLOSED イベントは発火しないため、
            # is_closed() を確認して QUIC 層に CONNECTION_CLOSE を送出しつつ
            # run() を終了する。H3 が閉じていれば必ず run を止めるため、
            # QUIC 側の状態は close() 呼び出し可否の判定にだけ使う
            if self._http3_connection.is_closed():
                await self._close_on_h3_error()

            await asyncio.sleep(0.01)

    async def migrate(self) -> bool:
        """ローカル UDP ソケットを差し替えてコネクションマイグレーションを開始する

        `quic.Client.migrate` と同じ手順。接続と上位層 (HTTP/3 / WebTransport)
        の状態は維持したまま、送受信に使うソケットとアドレスだけを差し替える。
        サーバー側は DCID で接続を照合してアドレスキーを張り替える
        (RFC 9000 Section 9)。

        Returns:
            マイグレーション開始に成功した場合は True
        """
        if self._quic_connection is None:
            return False

        # 現接続と同一 family で新ソケットを作る。リモートは接続時の数値
        # IP を使い回し、再解決しない
        if self._socket is not None:
            family = self._socket.family
        elif self._remote_addr is not None and ":" in self._remote_addr[0]:
            family = socket.AF_INET6
        else:
            family = socket.AF_INET
        new_socket = socket.socket(family, socket.SOCK_DGRAM)
        new_socket.setblocking(False)
        new_socket.bind(("::", 0) if family == socket.AF_INET6 else ("0.0.0.0", 0))
        new_local = self._normalize_addr(new_socket.getsockname())
        remote = self._remote_addr if self._remote_addr is not None else (self._host, self._port)

        if not self._quic_connection.initiate_migration(new_local, remote):
            new_socket.close()
            return False

        old_socket = self._socket
        self._socket = new_socket
        self._local_addr = new_local

        if old_socket is not None:
            old_socket.close()

        await self._send_pending()
        return True

    async def close(self) -> None:
        """接続を閉じる"""
        self._running = False
        self._connected = False

        if self._quic_connection is not None:
            self._quic_connection.close()
            await self._send_pending()

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
