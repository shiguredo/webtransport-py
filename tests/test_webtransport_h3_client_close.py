"""WebTransport over HTTP/3 クライアントの終了・送信停止の実通信テスト

高レベル Client (src/webtransport/_h3_client.py) の `close` と `stop_sending`
がワイヤに送出するものを、モックを使わず実 UDP 通信で観測する。高レベル
Server は受信した WT_CLOSE_SESSION の終了コード・理由をアプリへ公開しない
ため、低レベル API (quic.Connection + h3.Session) で組んだサーバーピアを
このファイル内に置く。

観測点は次の 2 つである。
- WT_CLOSE_SESSION をピアが SESSION_CLOSED イベントの error_code /
  error_message として復元できること (draft-ietf-webtrans-http3-16 Section 6)
- STOP_SENDING のアプリケーションエラーコードが WT_APPLICATION_ERROR
  レンジへリマップされてワイヤに載ること (同 Section 4.4 の MUST)
"""

import asyncio
import socket

import pytest

from webtransport import h3 as h3_low
from webtransport import quic
from webtransport._h3_client import Client
from webtransport._h3_uni_streams import bind_http3_uni_streams, open_http3_uni_streams
from webtransport._udp_socket import recv_datagram

# 待機の上限 (秒)。テスト全体は pytest-timeout の 30 秒で打ち切られるため、
# 個々の待機はこの上限で必ず終わらせる
_WAIT_LIMIT = 5.0

# アプリケーションエラーコード 3 をリマップした後のワイヤコード。
# WT_APPLICATION_ERROR レンジの基準値 0x52e4a40fa8db にアプリコードを足し、
# 予約済みコードポイント (0x1f * N + 0x21) をスキップした値である
# (draft-ietf-webtrans-http3-16 Section 4.4 / Section 9.5 の Figure 4)。
# 3 // 0x1e == 0 のためスキップは発生せず、基準値 + 3 になる
_REMAPPED_ERROR_CODE_FOR_APPLICATION_CODE_3 = 0x52E4A40FA8DE

# close() の引数を省略した場合のデフォルト値 (従来挙動)
_DEFAULT_ERROR_CODE = 0
_DEFAULT_ERROR_MESSAGE = ""


class _LowLevelServer:
    """低レベル API (quic.Connection + h3.Session) で構築するサーバーピア

    tests/test_e2e_webtransport_h3_low_level.py の _LowLevelClient を逆向き
    (サーバー役) にした実装である。接続の受け入れ手順は高レベル Server の
    _create_connection、制御ストリームの開設は _setup_streams、イベント処理は
    _process_quic_events / _process_webtransport_events、送信は _send_to を
    参考にしている。

    クライアントが送出した WT_CLOSE_SESSION の終了コードと理由、および
    STOP_SENDING のワイヤコードを記録する。これらは高レベル Server の
    コールバックからは観測できない。
    """

    def __init__(self, certfile: str, keyfile: str) -> None:
        self._certfile = certfile
        self._keyfile = keyfile
        self._socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind(("127.0.0.1", 0))
        self._local_addr: tuple[str, int] = (
            "127.0.0.1",
            self._socket.getsockname()[1],
        )
        # 接続を確立したピアのアドレス (未確立なら None)
        self._remote_addr: tuple[str, int] | None = None

        self._quic_connection: quic.Connection | None = None
        self._h3_session: h3_low.Session | None = None
        # HTTP/3 制御ストリームを開設済みかどうか
        self._streams_setup = False
        self._running = False

        # セッション確立の観測結果
        self.session_ids: list[int] = []
        self.session_ready_event: asyncio.Event = asyncio.Event()
        # WT_CLOSE_SESSION 受信の観測結果 (終了コードと理由)
        self.closed_error_codes: list[int] = []
        self.closed_error_messages: list[str] = []
        self.session_closed_event: asyncio.Event = asyncio.Event()
        # データストリーム受信の観測結果 (STOP_SENDING の前提を作るために使う)
        self.stream_data_event: asyncio.Event = asyncio.Event()
        # QUIC STOP_SENDING 受信の観測結果 (stream_id, ワイヤの error code)
        self.stop_sending_events: list[tuple[int, int]] = []
        self.stop_sending_event: asyncio.Event = asyncio.Event()
        # ピアからの CONNECTION_CLOSE を観測したかどうか
        self.connection_closed = False

    @property
    def port(self) -> int:
        """バインドしている UDP ポート"""
        return self._local_addr[1]

    def close(self) -> None:
        """QUIC 接続とソケットを閉じる

        同期メソッドのため CONNECTION_CLOSE パケットは送出しない
        (_LowLevelClient.close と同じ扱い)
        """
        if self._quic_connection is not None:
            self._quic_connection.close()
        self._socket.close()

    def _accept_connection(
        self,
        initial_packet: bytes,
        remote_addr: tuple[str, int],
    ) -> None:
        """初期パケットから QUIC 接続を受け入れて h3 セッションを作る

        高レベル Server の _create_connection と同じ手順である。accept は
        接続を作るだけで初期パケットを処理しないため、続けて receive に渡す。
        """
        quic_config = quic.Config()
        quic_config.alpn_protocols = ["h3"]
        quic_config.cert_file = self._certfile
        quic_config.key_file = self._keyfile

        h3_config = h3_low.Config()
        h3_config.is_server = True

        self._remote_addr = remote_addr
        self._quic_connection = quic.Connection.accept(
            quic_config,
            initial_packet,
            self._local_addr,
            remote_addr,
        )
        self._quic_connection.receive(initial_packet, self._local_addr, remote_addr)
        self._h3_session = h3_low.Session.create_server(h3_config)

    def _setup_streams(self) -> None:
        """HTTP/3 の制御・QPACK ストリームを開いてバインドする

        高レベル Server の _setup_streams と同じ手順である。
        """
        if self._quic_connection is None or self._h3_session is None:
            return
        if self._streams_setup:
            return

        control_stream_id, encoder_stream_id, decoder_stream_id = open_http3_uni_streams(
            self._quic_connection
        )
        bind_http3_uni_streams(
            self._h3_session,
            control_stream_id,
            encoder_stream_id,
            decoder_stream_id,
        )

        # クライアントからの双方向ストリームを受け入れる準備
        self._h3_session.set_max_client_streams_bidi(100)

        self._streams_setup = True

    def _process_quic_events(self) -> bool:
        """QUIC イベントを処理して h3 層に流す

        Returns:
            接続が継続する場合は True
        """
        if self._quic_connection is None or self._h3_session is None:
            return False

        while True:
            quic_event = self._quic_connection.next_event()
            if quic_event is None:
                break

            if quic_event.type == quic.EventType.HANDSHAKE_COMPLETED:
                self._setup_streams()
            elif quic_event.type == quic.EventType.STREAM_DATA:
                self._h3_session.receive_stream_data(
                    quic_event.stream_id,
                    quic_event.data,
                    quic_event.fin,
                )
            elif quic_event.type == quic.EventType.DATAGRAM:
                self._h3_session.receive_datagram(quic_event.data)
            elif quic_event.type == quic.EventType.STREAM_RESET:
                self._h3_session.close_stream(
                    quic_event.stream_id,
                    quic_event.error_code,
                )
            elif quic_event.type == quic.EventType.STOP_SENDING:
                # ワイヤに載った error code をそのまま記録する。リマップの
                # 検証点はここであり、h3 層を経由させない
                self.stop_sending_events.append((quic_event.stream_id, quic_event.error_code))
                self.stop_sending_event.set()
            elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                self.connection_closed = True
                return False

        return True

    def _process_webtransport_events(self) -> None:
        """WebTransport イベントを処理する

        SESSION_CLOSED では WT_CLOSE_SESSION の終了コードと理由を記録し、
        CONNECT ストリームへ FIN を送ってセッション終了のハンドシェイクを
        完了させる (高レベル Server の _process_webtransport_events と同じ。
        応答しないとピアの close() がピア終了待ちの上限まで待つ)。
        """
        if self._quic_connection is None or self._h3_session is None:
            return

        while True:
            webtransport_event = self._h3_session.next_event()
            if webtransport_event is None:
                break

            if webtransport_event.type == h3_low.EventType.SESSION_READY:
                # セッションを受理して 2xx 応答を返す
                self._h3_session.accept_session(webtransport_event.session_id)
                self.session_ids.append(webtransport_event.session_id)
                self.session_ready_event.set()
            elif webtransport_event.type == h3_low.EventType.SESSION_CLOSED:
                # 受信した WT_CLOSE_SESSION の終了コードと理由を記録する
                self.closed_error_codes.append(webtransport_event.error_code)
                self.closed_error_messages.append(webtransport_event.error_message)
                self.session_closed_event.set()
                self._quic_connection._send_stream_data_unchecked(
                    webtransport_event.session_id,
                    b"",
                    fin=True,
                )
            elif webtransport_event.type == h3_low.EventType.STREAM_DATA:
                self.stream_data_event.set()
            elif webtransport_event.type == h3_low.EventType.RESET_STREAM:
                self._quic_connection.reset_stream(
                    webtransport_event.stream_id,
                    webtransport_event.error_code,
                )
            elif webtransport_event.type == h3_low.EventType.STOP_SENDING:
                self._quic_connection.stop_sending(
                    webtransport_event.stream_id,
                    webtransport_event.error_code,
                )

    async def _pump(self) -> None:
        """h3 層の送信データを QUIC へ流して送信する

        高レベル Server の _send_to と同じく、上位層が組み立てたワイヤデータは
        _send_stream_data_unchecked で渡す。
        """
        if self._quic_connection is None or self._h3_session is None:
            return
        if self._remote_addr is None:
            return

        for stream_id, stream_data, fin in self._h3_session.get_streams_to_send():
            self._quic_connection._send_stream_data_unchecked(stream_id, stream_data, fin)

        loop = asyncio.get_running_loop()
        while True:
            packet = self._quic_connection.send()
            if packet is None:
                return
            await loop.sock_sendto(self._socket, packet.data, self._remote_addr)

    async def run(self) -> None:
        """実 UDP ソケットでパケットを受信して h3 層へ流す

        待機には recv_datagram を使う。asyncio.wait_for で loop.sock_recvfrom を
        包むと macOS の kqueue セレクタでパケットの読み取り可能通知が失われる
        (src/webtransport/_udp_socket.py の wait_socket_readable 参照。
        _LowLevelClient._receive と同じ理由)。
        """
        self._running = True
        while self._running:
            result = await recv_datagram(self._socket, 0.1)
            if result is not None:
                data, raw_remote = result
                remote = (str(raw_remote[0]), raw_remote[1])
                if self._quic_connection is None:
                    self._accept_connection(data, remote)
                else:
                    self._quic_connection.receive(data, self._local_addr, remote)

            if self._quic_connection is None:
                continue

            if not self._process_quic_events():
                self._running = False
                break
            self._process_webtransport_events()

            # 満了した QUIC タイマーを進めてから送信する (高レベル Server の
            # run() と同じ駆動)。進めないと pacing で止まったパケットが送信
            # キューに残り、WT_CLOSE_SESSION への応答 FIN が届かない
            timeout_ns = self._quic_connection.get_timeout()
            if timeout_ns is not None and timeout_ns <= 0:
                self._quic_connection.handle_timeout()
            await self._pump()


async def _cleanup_low_level_server(
    server: _LowLevelServer,
    server_task: asyncio.Task[None],
    client: Client,
) -> None:
    """クライアントとサーバーピアを後片付けする

    クライアントを先に閉じる。生存中のサーバーピアが CONNECT ストリームの
    FIN を返すため、close() のピア終了待ちが上限まで待たずに完了する。
    サーバータスクが例外終了していた場合は、テスト本体の失敗を覆い隠さない
    よう元の例外を raise する。
    """
    try:
        await asyncio.wait_for(client.close(), timeout=_WAIT_LIMIT)
    finally:
        if server_task.done() and not server_task.cancelled():
            exception = server_task.exception()
            if exception is not None:
                raise exception
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        server.close()


@pytest.mark.asyncio
async def test_close_sends_wt_close_session_with_error_code_and_message(test_certificates):
    """close() が指定した終了コードと理由を持つ WT_CLOSE_SESSION を送出することを確認

    低レベル API で組んだサーバーピアは、高レベル Server が公開しない
    WT_CLOSE_SESSION の終了コードと理由を SESSION_CLOSED イベントとして観測
    できる。クライアントの close(7, "protocol violation") が送出した
    WT_CLOSE_SESSION を、ピアが error_code 7 / error_message
    "protocol violation" として復元できることを確認する
    (draft-ietf-webtrans-http3-16 Section 6)
    """
    server = _LowLevelServer(
        test_certificates["certfile"],
        test_certificates["keyfile"],
    )
    server_task = asyncio.create_task(server.run())

    client = Client(
        url=f"https://127.0.0.1:{server.port}/webtransport",
        verify_peer=False,
    )

    try:
        # WebTransport セッションを確立する
        await asyncio.wait_for(client.connect(), timeout=_WAIT_LIMIT)
        await asyncio.wait_for(server.session_ready_event.wait(), timeout=_WAIT_LIMIT)
        assert server.session_ids == [client.session_id]

        # 終了コードと理由を指定して閉じる。close() はピアの CONNECT ストリーム
        # 終了を待ってから戻るため、この時点でピアは WT_CLOSE_SESSION を
        # 受信済みである
        await asyncio.wait_for(
            client.close(7, "protocol violation"),
            timeout=_WAIT_LIMIT,
        )

        # ピアが SESSION_CLOSED を 1 回だけ、指定した終了コードと理由で観測する
        await asyncio.wait_for(server.session_closed_event.wait(), timeout=_WAIT_LIMIT)
        assert server.closed_error_codes == [7]
        assert server.closed_error_messages == ["protocol violation"]
    finally:
        await _cleanup_low_level_server(server, server_task, client)


@pytest.mark.asyncio
async def test_close_without_arguments_sends_default_error_code_and_message(test_certificates):
    """引数省略の close() が終了コード 0 と空文字列を送出することを確認

    close() の引数を省略した場合は終了コード 0 (NO_ERROR) と空の理由が
    送出される (従来挙動の維持)。ピア側で error_code 0 / error_message
    "" として観測できることを確認する
    """
    server = _LowLevelServer(
        test_certificates["certfile"],
        test_certificates["keyfile"],
    )
    server_task = asyncio.create_task(server.run())

    client = Client(
        url=f"https://127.0.0.1:{server.port}/webtransport",
        verify_peer=False,
    )

    try:
        # WebTransport セッションを確立する
        await asyncio.wait_for(client.connect(), timeout=_WAIT_LIMIT)
        await asyncio.wait_for(server.session_ready_event.wait(), timeout=_WAIT_LIMIT)

        # 引数なしで閉じる
        await asyncio.wait_for(client.close(), timeout=_WAIT_LIMIT)

        # ピアが既定値 (0 と空文字列) を観測する
        await asyncio.wait_for(server.session_closed_event.wait(), timeout=_WAIT_LIMIT)
        assert server.closed_error_codes == [_DEFAULT_ERROR_CODE]
        assert server.closed_error_messages == [_DEFAULT_ERROR_MESSAGE]
    finally:
        await _cleanup_low_level_server(server, server_task, client)


@pytest.mark.asyncio
async def test_stop_sending_remaps_error_code_to_wt_application_error_range(
    test_certificates,
):
    """stop_sending() がリマップした STOP_SENDING を送出することを確認

    データストリームのアプリケーションエラーコードは WT_APPLICATION_ERROR
    レンジへリマップしてワイヤに載せる (draft-ietf-webtrans-http3-16
    Section 4.4 の MUST)。サーバーピアが QUIC の STOP_SENDING イベントとして
    観測する error code がアプリコード 3 ではなく、リマップ後の
    0x52e4a40fa8de になることを確認する
    """
    server = _LowLevelServer(
        test_certificates["certfile"],
        test_certificates["keyfile"],
    )
    server_task = asyncio.create_task(server.run())

    client = Client(
        url=f"https://127.0.0.1:{server.port}/webtransport",
        verify_peer=False,
    )

    try:
        # WebTransport セッションを確立する
        await asyncio.wait_for(client.connect(), timeout=_WAIT_LIMIT)
        await asyncio.wait_for(server.session_ready_event.wait(), timeout=_WAIT_LIMIT)

        # データストリームを開いてデータを送る。ピアにストリームを認識させて
        # からでないと、STOP_SENDING の対象がピア側のストリーム状態と
        # 一致しない
        stream_id = await asyncio.wait_for(client.open_stream(), timeout=_WAIT_LIMIT)
        assert stream_id >= 0
        await asyncio.wait_for(
            client.send_stream_data(stream_id, b"payload"),
            timeout=_WAIT_LIMIT,
        )
        await asyncio.wait_for(server.stream_data_event.wait(), timeout=_WAIT_LIMIT)

        # アプリケーションエラーコード 3 で送信停止を要求する
        await asyncio.wait_for(client.stop_sending(stream_id, 3), timeout=_WAIT_LIMIT)

        # ピアはクライアント起動の双方向ストリーム宛ての STOP_SENDING を、
        # リマップ後のワイヤコードで観測する
        await asyncio.wait_for(server.stop_sending_event.wait(), timeout=_WAIT_LIMIT)
        assert server.stop_sending_events == [
            (stream_id, _REMAPPED_ERROR_CODE_FOR_APPLICATION_CODE_3)
        ]
        # リマップが無ければアプリコード 3 がそのままワイヤに載る。その回帰を
        # 明示的に否定する
        assert server.stop_sending_events[0][1] != 3
    finally:
        await _cleanup_low_level_server(server, server_task, client)
