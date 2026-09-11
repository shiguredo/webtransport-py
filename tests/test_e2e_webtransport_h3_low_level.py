"""webtransport.h3 (WebTransport over HTTP/3) 低レベル API テスト

低レベル API (quic.Connection + h3.Session) を使う検証を置く。同一 QUIC
接続上に複数セッションを確立する検証を含む。高レベル API (Client / Server)
のテストは test_e2e_webtransport_h3.py に置く。
"""

import asyncio
import socket
from dataclasses import dataclass, field

import pytest
from conftest import _encode_wt_datagram

from webtransport import h3 as h3_low
from webtransport import quic
from webtransport.h3 import Server


class _LowLevelClient:
    """低レベル API (quic.Connection + h3.Session) で構築するクライアント

    高レベル Client は 1 接続 1 セッションのため、同一 QUIC 接続上に
    複数の WebTransport セッションを確立する検証には低レベル API を使う。
    接続手順は高レベル Client の connect (src/webtransport/h3/client.py) を
    参考にしている
    """

    def __init__(self, server_port: int) -> None:
        self._server_addr: tuple[str, int] = ("127.0.0.1", server_port)
        self._socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind(("127.0.0.1", 0))
        self._local_addr: tuple[str, int] = (
            "127.0.0.1",
            self._socket.getsockname()[1],
        )

        quic_config = quic.Config()
        quic_config.alpn_protocols = ["h3"]
        quic_config.verify_peer = False
        quic_config.server_name = "127.0.0.1"
        self._quic_connection: quic.Connection = quic.Connection.create_client(
            quic_config,
            self._local_addr,
            self._server_addr,
        )
        h3_config = h3_low.Config()
        h3_config.is_server = False
        self._h3_session: h3_low.Session = h3_low.Session.create_client(h3_config)

        # QUIC 層で生成済みだがワイヤに送出していないパケット
        # (RESET_STREAM_AT の検証で、データがリセットより先に届かない
        # 順序を作るために使う)
        self._withheld_packets: list[quic.Packet] = []

    def close(self) -> None:
        """QUIC 接続とソケットを閉じる

        同期メソッドのため CONNECTION_CLOSE パケットは送出しない
        (サーバー側の終了検知はテストの後片付けが server.stop() で
        行うため、このクラスでは不要)
        """
        self._quic_connection.close()
        self._socket.close()

    async def _send_packet(self) -> None:
        """QUIC 層のパケットを送信する"""
        loop = asyncio.get_running_loop()
        packet = self._quic_connection.send()
        if packet is None:
            return
        await loop.sock_sendto(self._socket, packet.data, self._server_addr)

    async def _pump(self) -> None:
        """h3 層の送信データを QUIC に渡して送信する"""
        for stream_id, stream_data, fin in self._h3_session.get_streams_to_send():
            self._quic_connection.send_stream_data(stream_id, stream_data, fin)
        await self._send_packet()

    async def _send_quic_only(self) -> None:
        """QUIC 層の送信だけを実行する

        h3 層の get_streams_to_send を呼ばないため、h3 層に積まれた
        WT ヘッダーはワイヤに出ない。データ未受信のままリセットする
        検証で使う
        """
        await self._send_packet()

    async def _receive(self) -> None:
        """QUIC パケットを 1 件受信して処理する (タイムアウト時は何もしない)"""
        loop = asyncio.get_running_loop()
        try:
            data, raw_remote = await asyncio.wait_for(
                loop.sock_recvfrom(self._socket, 65535),
                timeout=0.1,
            )
        except TimeoutError:
            return
        remote = (str(raw_remote[0]), raw_remote[1])
        self._quic_connection.receive(data, self._local_addr, remote)

    def _process_quic_events(self) -> bool:
        """QUIC イベントを処理して h3 層に流す

        Returns:
            接続が継続する場合は True
        """
        while True:
            quic_event = self._quic_connection.next_event()
            if quic_event is None:
                break
            if quic_event.type == quic.EventType.STREAM_DATA:
                self._h3_session.receive_stream_data(
                    quic_event.stream_id,
                    quic_event.data,
                    quic_event.fin,
                )
            elif quic_event.type == quic.EventType.DATAGRAM:
                self._h3_session.receive_datagram(quic_event.data)
            elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                return False
        return True

    async def connect(self) -> bool:
        """QUIC ハンドシェイク、制御ストリームのバインド、サーバーの SETTINGS 受信を行う

        Returns:
            接続に成功した場合は True
        """
        await self._pump()
        handshake_done = False
        while not handshake_done:
            await self._receive()
            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break
                if quic_event.type == quic.EventType.HANDSHAKE_COMPLETED:
                    handshake_done = True
                    # 以降のイベント (サーバーの SETTINGS 等) は
                    # 次の SETTINGS 待ちループで処理する
                    break
                elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                    return False
            await self._pump()

        control_stream_id = self._quic_connection.open_stream(False)
        self._h3_session.bind_control_stream(control_stream_id)
        encoder_stream_id = self._quic_connection.open_stream(False)
        self._h3_session.bind_qpack_encoder_stream(encoder_stream_id)
        decoder_stream_id = self._quic_connection.open_stream(False)
        self._h3_session.bind_qpack_decoder_stream(decoder_stream_id)
        await self._pump()

        # サーバーの SETTINGS を受信するまで待機する。制御ストリーム ID に
        # 依存せず、is_webtransport_ready() が SETTINGS の 3 設定
        # (wt_enabled / enable_connect_protocol / h3_datagram) を直接判定する
        # (高レベル Client の connect と同じ)
        max_attempts = 100
        attempt = 0
        while not self._h3_session.is_webtransport_ready() and attempt < max_attempts:
            await self._receive()
            while True:
                quic_event = self._quic_connection.next_event()
                if quic_event is None:
                    break
                if quic_event.type == quic.EventType.STREAM_DATA:
                    self._h3_session.receive_stream_data(
                        quic_event.stream_id,
                        quic_event.data,
                        quic_event.fin,
                    )
                elif quic_event.type == quic.EventType.CONNECTION_CLOSED:
                    return False
            await self._pump()
            attempt += 1
        return self._h3_session.is_webtransport_ready()

    async def establish_session(self) -> int:
        """WebTransport セッションを確立してセッション ID を返す

        Returns:
            セッション ID。接続が閉じた場合は -1
        """
        request_stream_id = self._quic_connection.open_stream(True)
        assert (
            self._h3_session.connect(
                request_stream_id,
                f"https://127.0.0.1:{self._server_addr[1]}/webtransport",
            )
            is True
        )
        await self._pump()

        while True:
            await self._receive()
            if not self._process_quic_events():
                return -1
            while True:
                h3_event = self._h3_session.next_event()
                if h3_event is None:
                    break
                if h3_event.type == h3_low.EventType.SESSION_READY:
                    return h3_event.session_id
            await self._pump()

    async def establish_two_sessions(self) -> tuple[int, int]:
        """同一 QUIC 接続上に 2 セッションを確立する

        Returns:
            (1 つ目のセッション ID, 2 つ目のセッション ID)
        """
        first_session_id = await asyncio.wait_for(self.establish_session(), timeout=5.0)
        second_session_id = await asyncio.wait_for(self.establish_session(), timeout=5.0)
        assert first_session_id >= 0
        assert second_session_id >= 0
        assert first_session_id != second_session_id
        return first_session_id, second_session_id

    async def open_stream(self, session_id: int) -> int:
        """セッションに双方向データストリームを開く

        WT ヘッダーは h3 層のキューに積まれるだけで、この時点では送信しない。
        送信は send_stream_data / reset_stream の QUIC 側送出に依存する。
        -1 検証テストの決定的性はこの「送信しない」前提に依存している
        (送信するとサーバー側の stream_info_ に登録され、セッション ID が
        復元可能になる)

        Returns:
            ストリーム ID
        """
        stream_id = self._quic_connection.open_stream(True)
        assert self._h3_session.open_stream(session_id, stream_id, False) is True
        return stream_id

    async def send_stream_data(self, stream_id: int, data: bytes) -> None:
        """ストリームにデータを送信する"""
        self._h3_session.send_stream_data(stream_id, data)
        await self._pump()

    async def send_stream_data_withheld(self, stream_id: int, data: bytes) -> None:
        """ストリームにデータを送信するが、生成したパケットは送出せず保持する

        QUIC 層 (ngtcp2) にデータを書き込み済み (tx offset が前進) にする一方、
        ワイヤには出さない。データがリセットより先に届かない順序を作る
        RESET_STREAM_AT の検証で使う。1 回の send() は 1 パケットしか返さない
        ため、データは 1 パケットに収まるサイズを渡すこと。この検証の決定的性
        は、データストリームより小さい ID のストリーム (CONNECT リクエスト /
        制御ストリーム) に残留データがないことにも依存する (send() は
        stream_buffers_ をストリーム ID 昇順で処理するため、残留があると
        データストリームのパケットが生成されず、データが stream_buffers_ に
        残ったままリセットで破棄される)
        """
        self._h3_session.send_stream_data(stream_id, data)
        for stream_id_to_send, stream_data, fin in self._h3_session.get_streams_to_send():
            self._quic_connection.send_stream_data(stream_id_to_send, stream_data, fin)
        packet = self._quic_connection.send()
        # パケットが生成されない場合 (cwnd 枯渇等) は、データが stream_buffers_
        # に残ったままリセットで破棄され、失敗モードが不明瞭になるため
        # ここで明示的に失敗させる
        assert packet is not None
        self._withheld_packets.append(packet)

    async def send_withheld_packets(self) -> None:
        """保留していたパケットを送信する"""
        loop = asyncio.get_running_loop()
        for packet in self._withheld_packets:
            await loop.sock_sendto(self._socket, packet.data, self._server_addr)
        self._withheld_packets.clear()

    async def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセットする

        QUIC と h3 層の両方にリセットを通知し、QUIC 層の送信のみを
        実行する。h3 層の get_streams_to_send を呼ぶと積まれた WT
        ヘッダーが送信されてしまうため、WT ヘッダー未受信のまま
        リセットする検証が決定的でなくなる。

        データストリームのアプリエラーコードは高レベル API と同様に
        WT_APPLICATION_ERROR へリマップしてから QUIC に渡す
        (draft-ietf-webtrans-http3-16 Section 4.4)。
        """
        wire_error_code = self._h3_session.map_send_error_code(stream_id, error_code)
        self._quic_connection.reset_stream(stream_id, wire_error_code)
        self._h3_session.reset_stream(stream_id, error_code)
        await self._send_quic_only()

    async def _receive_datagram(self) -> h3_low.Event | None:
        """データグラムを受信して Datagram イベントを返す

        最大 5 秒待ち、受信できなかった場合は None を返す。Datagram より
        先に積まれた h3 イベント (セッション終了通知等) は消費して捨てる
        """
        deadline = asyncio.get_running_loop().time() + 5.0
        while True:
            await self._receive()
            if not self._process_quic_events():
                return None
            while True:
                event = self._h3_session.next_event()
                if event is None:
                    break
                if event.type == h3_low.EventType.DATAGRAM:
                    return event
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.01)


@dataclass
class _ResetTestServerInfo:
    """STREAM_RESET 検証用サーバーの観測結果"""

    session_ids: list[int] = field(default_factory=list)
    sessions_ready: asyncio.Event = field(default_factory=asyncio.Event)
    data_session_id: int | None = None
    data_received: asyncio.Event = field(default_factory=asyncio.Event)
    reset_session_id: int | None = None
    reset_stream_id: int | None = None
    reset_received: asyncio.Event = field(default_factory=asyncio.Event)


async def _start_reset_test_server(
    test_certificates,
    expected_sessions: int,
) -> tuple[Server, asyncio.Task, _ResetTestServerInfo]:
    """STREAM_RESET 検証用の高レベル Server を起動する

    Args:
        test_certificates: テスト用証明書フィクスチャ
        expected_sessions: セッション確立待ちの数

    Returns:
        (server, server_task, info) のタプル
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    info = _ResetTestServerInfo()

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        info.session_ids.append(session_id)
        if len(info.session_ids) == expected_sessions:
            info.sessions_ready.set()

    async def on_stream_data(
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        info.data_session_id = session_id
        info.data_received.set()

    async def on_stream_reset(
        session_id: int,
        stream_id: int,
        error_code: int,
        addr: tuple[str, int],
    ) -> None:
        info.reset_session_id = session_id
        info.reset_stream_id = stream_id
        info.reset_received.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
    server.on_stream_reset(on_stream_reset)

    # サーバーの起動を完了させてからタスクを作成する
    # (run() は未開始状態だと RuntimeError を上げるため)
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    return server, server_task, info


async def _cleanup_reset_test_server(
    server: Server,
    server_task: asyncio.Task,
    client: _LowLevelClient,
) -> None:
    """_LowLevelClient を使う e2e テストの後片付けを行う

    サーバータスクが例外終了していた場合は、テスト本体の失敗を
    覆い隠さないよう元の例外を raise する
    """
    if server_task.done():
        exception = server_task.exception()
        if exception is not None:
            raise exception
    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await server.stop()
    client.close()


@dataclass
class _SessionClosedServerInfo:
    """CONNECT ストリームのクローズ (リセット / FIN) によるセッション終了検知の観測結果"""

    session_ids: list[int] = field(default_factory=list)
    sessions_ready: asyncio.Event = field(default_factory=asyncio.Event)
    closed_session_ids: list[int] = field(default_factory=list)
    session_closed: asyncio.Event = field(default_factory=asyncio.Event)
    data_session_id: int | None = None
    data_received: asyncio.Event = field(default_factory=asyncio.Event)


async def _start_session_closed_server(
    test_certificates,
    expected_sessions: int,
) -> tuple[Server, asyncio.Task, _SessionClosedServerInfo]:
    """セッション終了検知検証用の高レベル Server を起動する

    on_session_ready / on_session_closed / on_stream_data を観測用のリストと
    イベントに記録する。sessions_ready は expected_sessions 件目の
    セッション確立で発火する。

    Returns:
        (server, server_task, info) のタプル
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    info = _SessionClosedServerInfo()

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        info.session_ids.append(session_id)
        if len(info.session_ids) == expected_sessions:
            info.sessions_ready.set()

    async def on_session_closed(session_id: int, addr: tuple[str, int]) -> None:
        info.closed_session_ids.append(session_id)
        info.session_closed.set()

    async def on_stream_data(
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        info.data_session_id = session_id
        info.data_received.set()

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    return server, server_task, info


@pytest.mark.asyncio
async def test_stream_reset_second_session_id(test_certificates):
    """複数セッション確立時にリセットしたストリームのセッション ID が渡ることを確認

    同一 QUIC 接続上に 2 セッションを確立し、2 つ目のセッションでクライアントが
    開いたデータストリームを、サーバー側の on_stream_data で受信を確認してから
    リセットすると、2 つ目のセッション ID が on_stream_reset に渡る
    (旧実装ではセッション ID 集合の先頭要素が渡っていた)
    """
    server, server_task, info = await _start_reset_test_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        await asyncio.wait_for(client.connect(), timeout=5.0)

        # 同一 QUIC 接続上に 2 セッションを確立する
        first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)
        assert info.session_ids == [first_session_id, second_session_id]

        # 2 つ目のセッションでデータストリームを開いて送信する
        stream_id = await client.open_stream(second_session_id)
        await client.send_stream_data(stream_id, b"payload")

        # サーバー側の受信を確認してからリセットする
        await asyncio.wait_for(info.data_received.wait(), timeout=5.0)
        assert info.data_session_id == second_session_id

        await client.reset_stream(stream_id)

        # リセットされたストリームの属するセッション ID が渡る
        await asyncio.wait_for(info.reset_received.wait(), timeout=5.0)
        assert info.reset_stream_id == stream_id
        assert info.reset_session_id == second_session_id
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_stream_reset_at_recovers_session_id(test_certificates):
    """書き込み済みデータのあるストリームのリセットでセッション ID が復元される

    データパケットを保留してリセット送出パケットを先に届ける構成で、
    RESET_STREAM_AT (draft-ietf-webtrans-http3-16 Section 4.4 の MUST) により
    セッション ID が復元されることを確認する。RESET_STREAM_AT の Reliable Size
    は書き込み済みオフセット全体に設定されるため、ピアはデータ到着まで
    リセットを確定しない (draft-ietf-quic-reliable-stream-reset-09 Section 5.3
    の Size Known → Data Recvd 遷移)。後から届いた WT ヘッダーでストリームが
    セッションに関連付けられてからリセットが確定し、on_stream_reset に正しい
    セッション ID が渡る (データ未送信のままリセットした場合は -1 になる。
    test_stream_reset_before_data_received_minus_one 参照)
    """
    server, server_task, info = await _start_reset_test_server(
        test_certificates, expected_sessions=1
    )

    client = _LowLevelClient(server.actual_port)
    try:
        await asyncio.wait_for(client.connect(), timeout=5.0)

        session_id = await client.establish_session()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)

        # データストリームを開いてデータを送信するが、パケットは保留する
        # (QUIC 層に書き込み済みの状態を作りつつ、データがリセットより先に
        # 届かない順序にする)
        stream_id = await client.open_stream(session_id)
        await client.send_stream_data_withheld(stream_id, b"payload")

        # リセット送出パケットを先に送信する (RESET_STREAM_AT)
        await client.reset_stream(stream_id)

        # 保留していたデータパケットを送信する。ngtcp2 の writev_stream は
        # アプリのデータ (vec) を直接パケットに書く設計のため、リセット送出
        # パケットに未 ACK データは同梱されない。データはこの保留パケット
        # 経由で配信され、RESET_STREAM_AT の Reliable Size によりピアは
        # データ到着までリセットを確定しない
        await client.send_withheld_packets()

        # 後から届いた WT ヘッダーでセッション ID が復元される
        await asyncio.wait_for(info.reset_received.wait(), timeout=5.0)
        assert info.reset_stream_id == stream_id
        assert info.reset_session_id == session_id
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_stream_reset_before_data_received_minus_one(test_certificates):
    """WT ヘッダー未受信のままリセットされたストリームには -1 が渡ることを確認

    open_stream と reset_stream の間に送信処理を挟まない (WT ヘッダーが先に
    届くと stream_info_ に登録され、-1 が決定的にならない)。セッションとの
    関連付けはストリーム先頭のヘッダー経由のみであり (draft-ietf-webtrans-http3-16
    Section 4.4)、データ未書き込みのリセットは従来どおり RESET_STREAM が送出
    される (Reliable Size 0 の RESET_STREAM_AT は RESET_STREAM と等価。
    draft-ietf-quic-reliable-stream-reset-09 Section 5)。ヘッダー未受信のまま
    リセットされたストリームは復元できない。旧実装では無関係なセッション ID が
    渡っていたケース
    """
    server, server_task, info = await _start_reset_test_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        await asyncio.wait_for(client.connect(), timeout=5.0)

        _first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)

        # データストリームを開くが、データは送信しない
        stream_id = await client.open_stream(second_session_id)

        # 送信処理を挟まずにリセットする
        await client.reset_stream(stream_id)

        # セッション ID を復元できないため -1 が渡る
        await asyncio.wait_for(info.reset_received.wait(), timeout=5.0)
        assert info.reset_stream_id == stream_id
        assert info.reset_session_id == -1
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_stream_reset_connect_stream_session_id(test_certificates):
    """CONNECT ストリームのリセットでセッション ID が渡ることを確認

    2 つ目のセッションの CONNECT ストリーム (最小 ID でない CONNECT) を
    クライアントがリセットすると、セッション ID (= CONNECT ストリーム ID。
    draft-ietf-webtrans-http3-16 Section 2.2) が on_stream_reset に渡る
    """
    server, server_task, info = await _start_reset_test_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        await asyncio.wait_for(client.connect(), timeout=5.0)

        _first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)

        # 2 つ目のセッションの CONNECT ストリームをリセットする
        await client.reset_stream(second_session_id)

        # セッション ID (= CONNECT ストリーム ID) が渡る
        await asyncio.wait_for(info.reset_received.wait(), timeout=5.0)
        assert info.reset_stream_id == second_session_id
        assert info.reset_session_id == second_session_id
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_connect_stream_reset_notifies_session_closed(test_certificates):
    """CONNECT ストリームのリセットでセッション終了が通知されることを確認

    同一 QUIC 接続上に 2 セッションを確立し、1 つ目のセッションの CONNECT
    ストリームをクライアントがリセットすると、on_session_closed が正しい
    セッション ID で 1 回だけ発火し、2 つ目のセッションのデータ送受信が
    継続できることを確認する (draft-ietf-webtrans-http3-16 Section 6 の
    セッション終了条件の 1 つ目)。旧実装では CONNECT ストリームのリセットで
    セッション ID が session_ids_ に残り続け、on_session_closed が発火
    しなかった
    """
    server, server_task, info = await _start_session_closed_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        await asyncio.wait_for(client.connect(), timeout=5.0)

        first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)
        assert info.session_ids == [first_session_id, second_session_id]

        # 1 つ目のセッションの CONNECT ストリームをリセットする
        await client.reset_stream(first_session_id, error_code=0x42)

        # on_session_closed が正しいセッション ID で 1 回だけ発火する
        await asyncio.wait_for(info.session_closed.wait(), timeout=5.0)
        assert info.closed_session_ids == [first_session_id]

        # クライアント側の SessionClosed イベントに error_code がローカル伝播する
        # (QUIC STREAM_RESET のアプリエラーコード)。1 回だけ発火することも
        # 確認する (サーバー側の closed_session_ids と対称)
        client_session_closed = None
        client_session_closed_count = 0
        while True:
            event = client._h3_session.next_event()
            if event is None:
                break
            if event.type == h3_low.EventType.SESSION_CLOSED:
                client_session_closed = event
                client_session_closed_count += 1
        assert client_session_closed_count == 1
        assert client_session_closed is not None
        assert client_session_closed.session_id == first_session_id
        assert client_session_closed.error_code == 0x42

        # 終了したセッションが session_ids_ から削除される
        assert client._h3_session.get_session_ids() == [second_session_id]

        # 2 つ目のセッションのデータ送受信が継続できることを確認する
        stream_id = await client.open_stream(second_session_id)
        await client.send_stream_data(stream_id, b"still-alive")

        await asyncio.wait_for(info.data_received.wait(), timeout=5.0)
        assert info.data_session_id == second_session_id
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_connect_stream_fin_notifies_session_closed(test_certificates):
    """CONNECT ストリームの FIN でセッション終了が通知されることを確認

    同一 QUIC 接続上に 2 セッションを確立し、1 つ目のセッションの CONNECT
    ストリームを空 FIN でクリーンクローズすると、on_session_closed が正しい
    セッション ID で 1 回だけ発火し、2 つ目のセッションのデータ送受信が
    継続できることを確認する (draft-ietf-webtrans-http3-16 Section 6 の
    セッション終了条件の 1 つ目)。旧実装では end_stream コールバックを
    登録しておらず、CONNECT ストリームの FIN でセッション ID が
    session_ids_ に残り続け、on_session_closed が発火しなかった
    """
    server, server_task, info = await _start_session_closed_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        await asyncio.wait_for(client.connect(), timeout=5.0)

        first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)
        assert info.session_ids == [first_session_id, second_session_id]

        # 1 つ目のセッションの CONNECT ストリームに空 FIN を直接注入して
        # 届ける (高レベル API には CONNECT ストリームへ FIN を送出する
        # 手段が無いため)
        client._quic_connection.send_stream_data(first_session_id, b"", fin=True)
        await client._send_quic_only()

        # on_session_closed が正しいセッション ID で 1 回だけ発火する
        await asyncio.wait_for(info.session_closed.wait(), timeout=5.0)
        assert info.closed_session_ids == [first_session_id]

        # サーバーからの応答 FIN を受信して、クライアント側の SessionClosed
        # イベントが発火するまで待つ (最大 5 秒。受信ループは _receive の
        # 0.1 秒タイムアウトで駆動する)。応答 FIN は server.py の
        # SESSION_CLOSED ハンドラによる QUIC 直接注入の 1 経路で届く。
        # error_code は 0 (クリーンクローズ。WT_CLOSE_SESSION 無しの FIN は
        # error code 0 かつ空のエラー文字列の WT_CLOSE_SESSION と等価。
        # draft-ietf-webtrans-http3-16 Section 6) で 1 回だけ発火すること
        # を確認する
        client_session_closed = None
        client_session_closed_count = 0
        deadline = asyncio.get_running_loop().time() + 5.0
        while client_session_closed is None:
            await client._receive()
            if not client._process_quic_events():
                break
            while True:
                event = client._h3_session.next_event()
                if event is None:
                    break
                if event.type == h3_low.EventType.SESSION_CLOSED:
                    client_session_closed = event
                    client_session_closed_count += 1
            if client_session_closed is None and asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.01)
        assert client_session_closed_count == 1
        assert client_session_closed is not None
        assert client_session_closed.session_id == first_session_id
        assert client_session_closed.error_code == 0

        # 終了したセッションが session_ids_ から削除される
        assert client._h3_session.get_session_ids() == [second_session_id]

        # 2 つ目のセッションのデータ送受信が継続できることを確認する
        stream_id = await client.open_stream(second_session_id)
        await client.send_stream_data(stream_id, b"still-alive")

        await asyncio.wait_for(info.data_received.wait(), timeout=5.0)
        assert info.data_session_id == second_session_id
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quarter_stream_id",
    [1 << 60, 1 << 61],
    ids=["2^60_positive_overflow", "2^61_negative"],
)
async def test_datagram_invalid_session_id_closes_connection(
    test_certificates,
    quarter_stream_id,
):
    """巨大な Quarter Stream ID を持つデータグラムでサーバーが接続を閉じることを確認する

    仕様逸脱ピアが巨大な Quarter Stream ID を持つデータグラムを送った場合、
    サーバーは H3_ID_ERROR (0x0108) で接続を閉じる (draft-ietf-webtrans-http3-16
    Section 4 の MUST)。負のセッション ID になる 2^61 以上と、正のまま範囲超過に
    なる 2^60 以上 2^61 未満の両方を検証する。不正なセッション ID は on_datagram
    に渡らない。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    datagram_received = asyncio.Event()

    async def on_datagram(session_id: int, data: bytes, addr: tuple[str, int]) -> None:
        datagram_received.set()

    server.on_datagram(on_datagram)

    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = _LowLevelClient(server.actual_port)
    try:
        await asyncio.wait_for(client.connect(), timeout=5.0)
        session_id = await client.establish_session()
        assert session_id >= 0

        # 巨大な Quarter Stream ID を 8 バイト varint でエンコードする
        # (RFC 9000 可変長整数)。2^60 以上 2^61 未満は正のまま範囲超過、
        # 2^61 以上は int64 のラップで負のセッション ID になる
        varint = (0xC0 << 56 | quarter_stream_id).to_bytes(8, "big")
        client._quic_connection.send_datagram(varint + b"huge-quarter-stream-id")
        # send() はストリームデータの後にデータグラムを書き込む (残留データが
        # あると ngtcp2 の MORE 契約により同一パケットに同梱される) ため、
        # 通常は 1 回のフラッシュで届く。残留ストリームデータの掃き出しを
        # 確実にする防御として複数回フラッシュする
        for _ in range(8):
            await client._send_packet()

        # サーバーが H3_ID_ERROR で接続を閉じる。CONNECTION_CLOSE を受信して
        # error_code() が 0x0108 になるまで待つ
        connection_closed = False
        for _ in range(100):
            await client._receive()
            if not client._process_quic_events():
                connection_closed = True
                break
            await asyncio.sleep(0.01)
        assert connection_closed is True
        assert client._quic_connection.error_code == 0x0108
        # 不正なセッション ID のデータグラムは on_datagram に渡らない
        assert datagram_received.is_set() is False

        # エントリ削除後に同一アドレスから追従パケット (非 Initial) が届いても
        # サーバーは黙って破棄して run() を継続する。未対策だと accept が
        # RuntimeError を投げてサーバータスクが例外終了する (遠隔 DoS の入口)
        loop = asyncio.get_running_loop()
        await loop.sock_sendto(
            client._socket,
            varint + b"huge-quarter-stream-id",
            ("127.0.0.1", server.actual_port),
        )
        await asyncio.sleep(0.05)
        assert server_task.done() is False
    finally:
        await _cleanup_reset_test_server(server, server_task, client)


@pytest.mark.asyncio
async def test_datagram_closed_session_id_discarded(test_certificates):
    """閉じたセッションの ID 宛てのデータグラムが破棄されることを確認

    終了したセッション ID 宛のデータグラムはアプリに配信されない
    (実装ポリシー。draft-ietf-webtrans-http3-16 Section 4 の「closed session
    宛のデータの扱いは Section 6 に従う」と、データグラムは再送されず配信
    保証がないこと (Section 4.1 / RFC 9221) が根拠)。セッション ID の構造
    検証 (範囲外 ID の H3_ID_ERROR) は維持され、閉じたセッションの ID を
    含む正常なセッション ID のデータグラムで接続が閉じないことを併せて
    確認する。
    """
    server, server_task, info = await _start_session_closed_server(
        test_certificates, expected_sessions=2
    )

    client = _LowLevelClient(server.actual_port)
    try:
        await asyncio.wait_for(client.connect(), timeout=5.0)

        first_session_id, second_session_id = await client.establish_two_sessions()

        await asyncio.wait_for(info.sessions_ready.wait(), timeout=5.0)
        assert info.session_ids == [first_session_id, second_session_id]

        # サーバー側のクライアントアドレスを取得する (接続は 1 つだけ)
        (client_addr,) = server._clients.keys()

        # 1 つ目のセッションを WT_CLOSE_SESSION で閉じる
        client._h3_session.close_session(first_session_id)
        await client._pump()

        # サーバー側のセッション終了を待つ
        await asyncio.wait_for(info.session_closed.wait(), timeout=5.0)
        assert info.closed_session_ids == [first_session_id]

        # クライアント側でもセッションが閉じたことを確認する
        assert client._h3_session.get_session_ids() == [second_session_id]

        # 閉じたセッションの ID 宛てのデータグラムは破棄される
        # (受信側の検証。送信側の高レベル send_datagram は終了した
        # セッションへの送信を無視するため、QUIC 層へのワイヤ形式
        # 直接注入で検証する)
        server_client = server._clients[client_addr]
        assert server_client.quic_connection is not None
        wire_datagram = _encode_wt_datagram(first_session_id, b"closed-dg")
        server_client.quic_connection.send_datagram(wire_datagram)
        # send() はストリームデータの後にデータグラムを書き込むため、
        # 残留ストリームデータがあるとデータグラムが次回のパケットに
        # 回り得る。確実に送出するため複数回フラッシュする
        for _ in range(8):
            await server._send_to(client_addr, server_client)
        # 破棄されるため、タイムアウトしても受信しない
        datagram_event = await client._receive_datagram()
        assert datagram_event is None

        # 構造検証は維持され、閉じたセッションの ID のデータグラムで
        # 接続が閉じない
        assert client._h3_session.is_closed() is False
        assert server_client.quic_connection.is_closed() is False

        # 開いているセッションの ID 宛てのデータグラムは従来どおり配送される
        await server.send_datagram(client_addr, second_session_id, b"open-dg")
        datagram_event = await client._receive_datagram()
        assert datagram_event is not None
        assert datagram_event.session_id == second_session_id
        assert datagram_event.data == b"open-dg"
    finally:
        await _cleanup_reset_test_server(server, server_task, client)
