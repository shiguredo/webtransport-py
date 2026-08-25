"""WebTransport over HTTP/3 クライアント

asyncio と UDP を使用した高レベル WebTransport クライアント実装。
"""

from __future__ import annotations

import asyncio
import socket
from typing import TYPE_CHECKING, Self

from webtransport import h3 as h3_low
from webtransport import quic
from webtransport.h3._transport_params import meets_transport_param_requirements

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Client:
    """WebTransport over HTTP/3 クライアント

    asyncio を使用した非同期 WebTransport クライアント。

    Usage:
        client = Client(url="https://localhost:4433/webtransport")
        await client.connect()
        await client.send_datagram(b"Hello")
        await client.close()

        # または
        async with Client(url="https://localhost:4433/webtransport") as client:
            await client.send_datagram(b"Hello")
    """

    def __init__(
        self,
        url: str,
        verify_peer: bool = True,
        origin: str = "",
        idle_timeout_ns: int = 30_000_000_000,
        ca_file: str | None = None,
        verify_callback: Callable[[list[bytes]], bool] | None = None,
        quic_config: quic.Config | None = None,
    ) -> None:
        """クライアントを初期化する

        Args:
            url: WebTransport エンドポイント URL
            verify_peer: サーバー証明書を検証するかどうか
            origin: Origin ヘッダー値 (空なら付与しない)
            idle_timeout_ns: アイドルタイムアウト (ナノ秒)
            ca_file: CA 証明書ファイルパス
            verify_callback: ピア証明書検証コールバック
            quic_config: QUIC 設定。省略時は既定値。alpn_protocols /
                idle_timeout_ns / verify_peer / server_name はコンストラクタ
                引数の値で接続時に上書きされる。enable_datagram /
                enable_reset_stream_at を無効化すると WebTransport の要件を
                満たさないピアを作れる (テスト用)
        """
        self._url = url
        self._verify_peer = verify_peer
        self._origin = origin
        self._idle_timeout_ns = idle_timeout_ns
        self._ca_file = ca_file
        self._verify_callback = verify_callback
        self._user_quic_config = quic_config
        self._host, self._port, self._path = self._parse_url(url)

        self._quic_connection: quic.Connection | None = None
        self._webtransport_session: h3_low.Session | None = None
        self._socket: socket.socket | None = None
        # bind 後のローカルアドレス (host, port)
        self._local_addr: tuple[str, int] | None = None
        self._running = False
        self._session_id = -1
        self._connected = False
        # connect() が SESSION_READY を消費したときの引き継ぎバッファ。
        # run() のイベントループ開始時に先に処理し、コールバック登録の
        # 順序に依存せず on_session_ready を発火させる
        self._pending_session_ready: int | None = None

        self._on_session_ready: Callable[[int], Awaitable[None]] | None = None
        self._on_session_closed: Callable[[int], Awaitable[None]] | None = None
        self._on_stream_data: Callable[[int, bytes], Awaitable[None]] | None = None
        self._on_stream_reset: Callable[[int, int], Awaitable[None]] | None = None
        self._on_datagram: Callable[[bytes], Awaitable[None]] | None = None

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

        不正なセッション ID (QUIC ストリーム ID 範囲外) のデータグラムは
        `on_datagram` に渡らず、接続が H3_ID_ERROR で閉じられる
        (draft-ietf-webtrans-http3-16 Section 4 の MUST)。終了した・一度も
        確立されていないセッション ID 宛のデータグラムは破棄される
        (実装ポリシー)。

        Args:
            callback: async def callback(data: bytes) -> None
        """
        self._on_datagram = callback

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

    def _normalize_addr(self, addr: tuple[object, ...]) -> tuple[str, int]:
        """recvfrom / getsockname のアドレスを (str, int) に正規化する"""
        host = addr[0]
        port = addr[1]
        if not isinstance(port, int):
            raise TypeError(f"expected port int, got {type(port).__name__}")
        return (str(host), port)

    def _destination_for_packet(
        self,
        packet: quic.Packet,
    ) -> tuple[str, int]:
        """パケットの送信先アドレスを決める"""
        if packet.remote_host and packet.remote_port:
            return (packet.remote_host, packet.remote_port)
        return (self._host, self._port)

    async def _send_pending(self) -> None:
        """送信待ちデータを送信する"""
        if self._quic_connection is None:
            return
        if self._webtransport_session is None:
            return
        if self._socket is None:
            return

        for stream_id, stream_data, fin in self._webtransport_session.get_streams_to_send():
            self._quic_connection.send_stream_data(stream_id, stream_data, fin)

        for datagram in self._webtransport_session.get_datagrams_to_send():
            self._quic_connection.send_datagram(datagram)

        # send() の連続 drain は ACK 待ちが必要なケースでハングするため 1 パケットに留める
        packet = self._quic_connection.send()
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
            remote = self._normalize_addr(raw_remote)
            self._quic_connection.receive(data, self._local_addr, remote)
        except TimeoutError:
            pass

    def _setup_streams(self) -> None:
        """HTTP/3 制御ストリームを設定する"""
        if self._quic_connection is None or self._webtransport_session is None:
            return

        control_stream_id = self._quic_connection.open_stream(False)
        self._webtransport_session.bind_control_stream(control_stream_id)

        encoder_stream_id = self._quic_connection.open_stream(False)
        self._webtransport_session.bind_qpack_encoder_stream(encoder_stream_id)

        decoder_stream_id = self._quic_connection.open_stream(False)
        self._webtransport_session.bind_qpack_decoder_stream(decoder_stream_id)

    async def connect(self) -> bool:
        """WebTransport セッションを確立する

        Returns:
            接続に成功した場合は True
        """
        quic_config = (
            self._user_quic_config if self._user_quic_config is not None else quic.Config()
        )
        quic_config.alpn_protocols = ["h3"]
        quic_config.idle_timeout_ns = self._idle_timeout_ns
        quic_config.verify_peer = self._verify_peer
        quic_config.server_name = self._host
        if self._ca_file is not None:
            quic_config.ca_file = self._ca_file
        if self._verify_callback is not None:
            quic_config.verify_callback = self._verify_callback

        webtransport_config = h3_low.Config()
        webtransport_config.is_server = False

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind(("0.0.0.0", 0))
        self._local_addr = self._normalize_addr(self._socket.getsockname())

        self._quic_connection = quic.Connection.create_client(
            quic_config,
            self._local_addr,
            (self._host, self._port),
        )
        self._webtransport_session = h3_low.Session.create_client(webtransport_config)

        await self._send_pending()
        self._running = True

        handshake_done = False
        while not handshake_done and self._running:
            await self._receive()

            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break

                if quic_event.type == quic.EventType.HANDSHAKE_COMPLETED:
                    handshake_done = True
                    break
                elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                    self._running = False
                    return False

            await self._send_pending()
            await asyncio.sleep(0.01)

        # サーバーの transport parameter を検証する
        # (draft-ietf-webtrans-http3-16 Section 3.1)。要件未達なら
        # CONNECT を送らずにセッションを確立しない。reset_stream_at の
        # 扱い (実ブラウザ互換のため必須としない) は
        # meets_transport_param_requirements の docstring を参照する
        if self._quic_connection is None or not meets_transport_param_requirements(
            self._quic_connection
        ):
            self._running = False
            return False

        self._setup_streams()
        await self._send_pending()

        # サーバーの SETTINGS を受信するまで待機
        settings_received = False
        max_attempts = 100
        attempt = 0
        while not settings_received and self._running and attempt < max_attempts:
            await self._receive()

            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break

                if quic_event.type == quic.EventType.STREAM_DATA:
                    # サーバーからの制御ストリームデータを処理
                    self._webtransport_session.receive_stream_data(
                        quic_event.stream_id,
                        quic_event.data,
                        quic_event.fin,
                    )
                    # サーバーの制御ストリーム (stream_id=3) からデータを受信したら設定完了とみなす
                    if quic_event.stream_id == 3:
                        settings_received = True
                elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                    self._running = False
                    return False

            await self._send_pending()
            await asyncio.sleep(0.01)
            attempt += 1

        if not settings_received:
            return False

        request_stream_id = self._quic_connection.open_stream(True)
        if self._webtransport_session.connect(request_stream_id, self._url, self._origin):
            self._session_id = request_stream_id
            await self._send_pending()

            # 2xx 応答 (または非 2xx 拒否) を待つ (draft-16 Section 3.2 の
            # 「From the client's perspective, a WebTransport session is
            # established when the client receives a 2xx response」)。
            # SESSION_READY (2xx 全般) で True、SESSION_REJECTED (非 2xx。
            # 1xx を含む) で False を返す (h2 側の connect と同型)。
            # SESSION_READY は run() のコールバック経路を確保するため
            # 未配信バッファへ引き継ぐ
            accepted = False
            for _ in range(100):
                # WebTransport セッションのイベントを確認する (2xx 応答の
                # 受信で SESSION_READY が発火する)
                while True:
                    event = self._webtransport_session.next_event()
                    if event is None:
                        break
                    if event.type == h3_low.EventType.SESSION_READY:
                        self._pending_session_ready = event.session_id
                        accepted = True
                        # 同一バッチの後続イベント (2xx + FIN 同時受信時の
                        # SESSION_CLOSED 等) で False にしないため、ドレイン
                        # を抜ける (残ったイベントはキューに残り、run() で
                        # 処理される)
                        break
                    if (
                        event.type == h3_low.EventType.SESSION_REJECTED
                        or event.type == h3_low.EventType.SESSION_CLOSED
                    ):
                        self._connected = False
                        return False
                if accepted:
                    break
                if self._quic_connection.is_closed():
                    return False
                # 受信した QUIC イベントを WebTransport セッションへ流す。
                # run() の _process_quic_events が処理する変換のうち、
                # connect() の応答待ちに必要な経路 (STREAM_DATA /
                # DATAGRAM / STREAM_RESET / CONNECTION_CLOSED) をここで
                # 行う (connect() 中は run() を実行できないため)
                await self._receive()
                while True:
                    quic_event = self._quic_connection.next_event()
                    if quic_event is None:
                        break
                    if quic_event.type == quic.EventType.STREAM_DATA:
                        self._webtransport_session.receive_stream_data(
                            quic_event.stream_id,
                            quic_event.data,
                            quic_event.fin,
                        )
                    elif quic_event.type == quic.EventType.DATAGRAM:
                        self._webtransport_session.receive_datagram(quic_event.data)
                    elif quic_event.type == quic.EventType.STREAM_RESET:
                        self._webtransport_session.close_stream(
                            quic_event.stream_id, quic_event.error_code
                        )
                    elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                        return False
                await self._send_pending()
                await asyncio.sleep(0.01)
            if not accepted:
                # 応答なし (応答が 2xx でも非 2xx でもなくタイムアウト) で
                # 確立できない。以後の run() は呼ばず close() する前提
                # (セッション ID の後始末は close() に依存する)
                return False

            self._connected = True
            return True

        return False

    async def open_stream(self, unidirectional: bool = False) -> int:
        """WebTransport ストリームを開く

        失敗した場合は -1 を返す。失敗条件は、セッション終了後・非 2xx
        拒否後・未確立 (connect 失敗後)・接続クローズ済みである。h3 層の
        登録に失敗した場合は、開いた QUIC ストリームを RESET_STREAM で
        解放してから -1 を返す (RESET_STREAM の送出は run() の送信ループ
        に委ねられる)。

        Args:
            unidirectional: 単方向ストリームにするかどうか

        Returns:
            ストリーム ID。失敗した場合は -1
        """
        if self._quic_connection is None or self._webtransport_session is None:
            return -1

        stream_id = self._quic_connection.open_stream(not unidirectional)
        if stream_id < 0:
            return -1

        # h3 層の登録に失敗した場合は、開いた QUIC ストリームをリセットして
        # -1 を返す (Server.open_stream と対称)。リセットしないとローカルの
        # ストリーム状態が接続終了まで open のまま残る。リセットすることで
        # ストリームを終了状態にし、ピアにもストリーム終了を通知する
        # (RFC 9000 Section 19.11 のストリーム数制限は接続の寿命全体に対す
        # る累積数であり、リセットしても回復しない)。h3 層への通知は不要。
        # ストリームは stream_info_ に未登録のためである
        if not self._webtransport_session.open_stream(self._session_id, stream_id, unidirectional):
            self._quic_connection.reset_stream(stream_id, 0)
            return -1

        return stream_id

    async def send_stream_data(
        self,
        stream_id: int,
        data: bytes,
        fin: bool = False,
    ) -> None:
        """ストリームにデータを送信する

        stream_info_ に未登録のストリーム (セッション ID を復元できない)
        への送信と、受信済みの単方向ストリーム (クライアント起点 %4==2 /
        サーバー起点 %4==3) への送信は黙って無視される。

        Args:
            stream_id: ストリーム ID
            data: 送信データ
            fin: ストリームを終了するか
        """
        if self._webtransport_session is None:
            return

        self._webtransport_session.send_stream_data(stream_id, data, fin)
        await self._send_pending()

    async def send_datagram(self, data: bytes) -> None:
        """データグラムを送信する

        終了したセッション ID への送信は無視される
        (draft-ietf-webtrans-http3-16 Section 6 の MUST)。

        Args:
            data: 送信データ
        """
        if self._webtransport_session is None:
            return

        self._webtransport_session.send_datagram(self._session_id, data)
        await self._send_pending()

    async def close_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームを閉じる (RESET_STREAM)

        Args:
            stream_id: ストリーム ID
            error_code: エラーコード
        """
        await self.reset_stream(stream_id, error_code)

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセットする (QUIC RESET_STREAM + nghttp3 通知)

        Args:
            stream_id: ストリーム ID
            error_code: エラーコード
        """
        if self._quic_connection is not None:
            self._quic_connection.reset_stream(stream_id, error_code)
        if self._webtransport_session is not None:
            self._webtransport_session.reset_stream(stream_id, error_code)
        await self._send_pending()

    async def _process_quic_events(self) -> bool:
        """QUIC イベントを処理する

        Returns:
            接続が継続する場合は True
        """
        if self._quic_connection is None or self._webtransport_session is None:
            return False

        while True:
            quic_event = self._quic_connection.next_event()
            if quic_event is None:
                break

            if quic_event.type == quic.EventType.STREAM_DATA:
                self._webtransport_session.receive_stream_data(
                    quic_event.stream_id,
                    quic_event.data,
                    quic_event.fin,
                )
            elif quic_event.type == quic.EventType.DATAGRAM:
                self._webtransport_session.receive_datagram(quic_event.data)
            elif quic_event.type == quic.EventType.STREAM_RESET:
                # 対向からの RESET_STREAM を nghttp3 に通知する
                self._webtransport_session.close_stream(
                    quic_event.stream_id,
                    quic_event.error_code,
                )
                if self._on_stream_reset is not None:
                    await self._on_stream_reset(
                        quic_event.stream_id,
                        quic_event.error_code,
                    )
            elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                return False

        return True

    async def _process_webtransport_events(self) -> None:
        """WebTransport イベントを処理する"""
        if self._webtransport_session is None or self._quic_connection is None:
            return

        while True:
            webtransport_event = self._webtransport_session.next_event()
            if webtransport_event is None:
                break

            if webtransport_event.type == h3_low.EventType.SESSION_READY:
                if self._on_session_ready is not None:
                    await self._on_session_ready(webtransport_event.session_id)

            elif webtransport_event.type == h3_low.EventType.SESSION_CLOSED:
                self._connected = False
                if self._on_session_closed is not None:
                    await self._on_session_closed(webtransport_event.session_id)

            elif webtransport_event.type == h3_low.EventType.STREAM_DATA:
                if self._on_stream_data is not None:
                    await self._on_stream_data(
                        webtransport_event.stream_id,
                        webtransport_event.data,
                    )

            elif webtransport_event.type == h3_low.EventType.DATAGRAM:
                if self._on_datagram is not None:
                    # receive_datagram が Quarter Stream ID から session_id を
                    # 復元し、セッション ID を検証する
                    # (draft-ietf-webtrans-http3-16 Section 4.5 / Section 4)。
                    # 範囲外のセッション ID は接続クローズとなり、終了した・
                    # 一度も確立されていないセッション ID 宛のデータグラムは
                    # 破棄されるため、ここに到達するのは生存セッションの
                    # データグラムのみである
                    await self._on_datagram(webtransport_event.data)

            elif webtransport_event.type == h3_low.EventType.RESET_STREAM:
                # nghttp3 が QUIC RESET_STREAM の送出を要求している
                self._quic_connection.reset_stream(
                    webtransport_event.stream_id,
                    webtransport_event.error_code,
                )

            elif webtransport_event.type == h3_low.EventType.STOP_SENDING:
                # nghttp3 が QUIC STOP_SENDING の送出を要求している
                self._quic_connection.stop_sending(
                    webtransport_event.stream_id,
                    webtransport_event.error_code,
                )

            elif webtransport_event.type == h3_low.EventType.ERROR:
                # データグラムの不正なセッション ID 受信 (H3_ID_ERROR) のみ
                # 接続クローズを扱う。receive_stream_data が生成する nghttp3
                # エラー (error_code が 0x0108 でない) は対象外
                if webtransport_event.error_code != 0x0108:
                    continue
                self._quic_connection.close(
                    webtransport_event.error_code,
                    webtransport_event.error_message,
                )
                await self._send_pending()
                # ローカル側のクローズ後は接続を終了扱いにして run() を終了させ、
                # 同一バッチに積まれた残りのイベント (クローズ後に配送される
                # データグラム等) の処理を打ち切る
                self._running = False
                self._connected = False
                break

    async def run(self) -> None:
        """メインループを実行する

        接続が終了するまでブロックする。
        """
        if self._quic_connection is None:
            raise RuntimeError("クライアントが接続されていません")

        # connect() が消費した SESSION_READY を引き継ぐ (コールバック登録の
        # 順序に依存せず、イベントループで発火させる)
        if self._pending_session_ready is not None:
            pending_session_id = self._pending_session_ready
            self._pending_session_ready = None
            if self._on_session_ready is not None:
                await self._on_session_ready(pending_session_id)

        while self._running:
            await self._receive()

            connection_alive = await self._process_quic_events()
            if not connection_alive:
                self._running = False
                break

            await self._process_webtransport_events()
            await self._send_pending()

            timeout = self._quic_connection.get_timeout()
            if timeout is not None and timeout <= 0:
                self._quic_connection.handle_timeout()

            await asyncio.sleep(0.01)

    async def close(self) -> None:
        """接続を閉じる"""
        # 未配信の SESSION_READY を破棄する (再 connect() の際に古い
        # セッション ID で発火させないため)
        self._pending_session_ready = None
        self._running = False
        self._connected = False

        if self._webtransport_session is not None and self._session_id >= 0:
            # WT_CLOSE_SESSION カプセルを先に送出してから QUIC を閉じる
            self._webtransport_session.close_session(self._session_id)
            await self._send_pending()
            self._session_id = -1

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
