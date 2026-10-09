"""QUIC サーバーのストリーム中断 API のテスト

高レベル `quic.Server` の `shutdown_stream` / `reset_stream` / `stop_sending` が
送出する QUIC フレーム (RESET_STREAM / STOP_SENDING) と、ピアの RESET_STREAM を
`on_stream_reset` で観測できることを実ソケットで検証する。ピアは低レベル
`quic.Connection` を実 UDP ソケットで駆動する Sans-IO ピアとし、フレームに
対応するイベントとアプリケーションエラーコードを直接観測する (モックなし)。
高レベル `quic.Client` から同じ操作を観測できることも併せて確認する。
"""

import asyncio
import socket
from collections.abc import Callable

import pytest

from webtransport._udp_socket import recv_datagram
from webtransport.quic import Client, Config, Connection, Event, EventType, Server

# ピアのポンプ試行上限。1 回の試行で送信待ちの掃き出しと受信 1 パケットを行う
PUMP_ATTEMPTS = 100
# ピアの受信待ちの上限 (秒)。サーバーの受信ループは 0.1 秒間隔で回るため、
# これより短いと空振りが増える
RECV_TIMEOUT = 0.2
# 目的のフレーム観測後に追加フレームの有無を確かめるための追加ポンプ回数
EXTRA_PUMP_STEPS = 5


async def _run_server(server: Server) -> None:
    """サーバーのメインループを実行する (キャンセルで終了)

    Args:
        server: 実行するサーバー
    """
    try:
        await server.run()
    except asyncio.CancelledError:
        pass


def _first_event(events: list[Event], event_type: EventType, stream_id: int) -> Event | None:
    """指定種別・ストリームの最初のイベントを返す (無ければ None)

    Args:
        events: 探索するイベント列
        event_type: 対象のイベント種別
        stream_id: 対象のストリーム ID

    Returns:
        最初に一致したイベント。一致が無ければ None
    """
    for event in events:
        if event.type == event_type and event.stream_id == stream_id:
            return event
    return None


class _LowLevelPeer:
    """実 UDP ソケットで低レベル Connection を駆動する Sans-IO ピア

    高レベル Server を相手に、実ソケットでハンドシェイクとパケット交換を行う。
    サーバーが送出したフレームは低レベル接続のイベントとして観測できるため、
    RESET_STREAM / STOP_SENDING とアプリケーションエラーコードを直接検証できる。
    """

    def __init__(self, server: Server) -> None:
        self._server_addr: tuple[str, int] = ("127.0.0.1", server.actual_port)
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._socket.bind(("127.0.0.1", 0))
        # getsockname() は IPv4 かつ 127.0.0.1 bind のため (str, int) になる
        self._local_addr: tuple[str, int] = self._socket.getsockname()

        config = Config()
        config.alpn_protocols = ["h3"]
        config.verify_peer = False
        config.server_name = "localhost"
        self._connection = Connection.create_client(config, self._local_addr, self._server_addr)

    @property
    def local_addr(self) -> tuple[str, int]:
        """サーバーから見たピアのアドレス"""
        return self._local_addr

    @property
    def connection(self) -> Connection:
        """ピアの低レベル接続"""
        return self._connection

    async def handshake(self, server_done: Callable[[], bool] | None = None) -> None:
        """サーバーとハンドシェイクを完了させる

        Args:
            server_done: サーバー側の準備完了を表す述語。省略時はピア側の
                ハンドシェイク完了だけで判定する
        """
        for _ in range(PUMP_ATTEMPTS):
            await self._step()
            self.drain()
            if not self._connection.is_handshake_completed():
                continue
            if server_done is None or server_done():
                return
        raise AssertionError("ピアのハンドシェイクが完了しませんでした")

    async def pump_until(self, done: Callable[[], bool]) -> list[Event]:
        """サーバー側の条件が満たされるまで送受信を繰り返す

        Args:
            done: サーバー側の状態を表す述語

        Returns:
            ポンプ中にピアが取り出したイベント列
        """
        events: list[Event] = []
        for _ in range(PUMP_ATTEMPTS):
            await self._step()
            events.extend(self.drain())
            if done():
                return events
        raise AssertionError("サーバー側の条件が満たされませんでした")

    async def pump_events(self, done: Callable[[list[Event]], bool]) -> list[Event]:
        """取り出したイベントが条件を満たすまで送受信を繰り返す

        Args:
            done: 取り出し済みイベント列を判定する述語

        Returns:
            条件を満たすまでにピアが取り出したイベント列
        """
        events: list[Event] = []
        for _ in range(PUMP_ATTEMPTS):
            await self._step()
            events.extend(self.drain())
            if done(events):
                return events
        raise AssertionError(
            f"ピアが期待するフレームを観測できませんでした: {[event.type for event in events]}"
        )

    async def pump_steps(self, count: int) -> list[Event]:
        """指定回数だけ送受信してイベントを取り出す (追加フレームの確認用)

        Args:
            count: ポンプ回数

        Returns:
            ポンプ中にピアが取り出したイベント列
        """
        events: list[Event] = []
        for _ in range(count):
            await self._step()
            events.extend(self.drain())
        return events

    def drain(self) -> list[Event]:
        """未処理のイベントを全て取り出す

        Returns:
            取り出したイベント列
        """
        events: list[Event] = []
        while True:
            event = self._connection.next_event()
            if event is None:
                return events
            events.append(event)

    async def _step(self) -> None:
        """送信待ちの掃き出しと受信 1 パケットの処理を 1 回行う

        送信の前に満了済みの QUIC タイマーを処理する。受信待ちには
        `recv_datagram` を使う (macOS の kqueue セレクタで
        `asyncio.wait_for` に包んだ受信が通知を失う問題を避けるため)。
        """
        timeout_ns = self._connection.get_timeout()
        if timeout_ns is not None and timeout_ns <= 0:
            self._connection.handle_timeout()
        while True:
            packet = self._connection.send()
            if packet is None:
                break
            self._socket.sendto(packet.data, self._server_addr)

        result = await recv_datagram(self._socket, RECV_TIMEOUT)
        if result is None:
            return
        data, _ = result
        self._connection.receive(data, self._local_addr, self._server_addr)

    def close(self) -> None:
        """ソケットを閉じる (テストの後片付け)"""
        self._socket.close()


@pytest.mark.asyncio
async def test_shutdown_stream_sends_reset_stream_and_stop_sending(test_certificates) -> None:
    """shutdown_stream が RESET_STREAM と STOP_SENDING を送出することを確認する

    双方向ストリームでは ngtcp2_conn_shutdown_stream が write 側と read 側の
    両方を shutdown するため、ピアは RESET_STREAM と STOP_SENDING の両方を
    観測する。ピアは低レベル接続を実ソケットで駆動し、両フレームのストリーム
    ID とアプリケーションエラーコードをイベントとして観測する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    stream_data_received = asyncio.Event()
    server_handshake_completed = asyncio.Event()

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_handshake_completed.set()

    async def on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None:
        # 応答は送らない (サーバーはデータ受信でストリームを認識する)
        stream_data_received.set()

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = _LowLevelPeer(server)
    try:
        # ハンドシェイクを完了させる (サーバー側の完了も待つ)
        await peer.handshake(lambda: server_handshake_completed.is_set())

        # ピアが双方向ストリームを開いてデータを送り、サーバーへストリームを
        # 認識させる
        stream_id = peer.connection.open_stream(bidirectional=True)
        assert stream_id >= 0
        peer.connection.send_stream_data(stream_id, b"ping", False)
        await peer.pump_until(stream_data_received.is_set)

        # サーバーがストリームを中断する (アプリケーションエラーコード付き)
        error_code = 0x2A
        await server.shutdown_stream(peer.local_addr, stream_id, error_code=error_code)

        # ピアが RESET_STREAM と STOP_SENDING の両方を観測する
        def both_frames_observed(events: list[Event]) -> bool:
            reset = _first_event(events, EventType.STREAM_RESET, stream_id)
            stop_sending = _first_event(events, EventType.STOP_SENDING, stream_id)
            return reset is not None and stop_sending is not None

        events = await peer.pump_events(both_frames_observed)
        reset_event = _first_event(events, EventType.STREAM_RESET, stream_id)
        stop_sending_event = _first_event(events, EventType.STOP_SENDING, stream_id)
        assert reset_event is not None, "RESET_STREAM が観測できるべき"
        assert stop_sending_event is not None, "STOP_SENDING が観測できるべき"
        assert reset_event.error_code == error_code
        assert stop_sending_event.error_code == error_code
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        peer.close()
        await server.stop()


@pytest.mark.asyncio
async def test_reset_stream_sends_only_reset_stream(test_certificates) -> None:
    """reset_stream が RESET_STREAM だけを送出することを確認する

    reset_stream は自身の送信側だけを中断する QUIC フレーム層の操作であり、
    STOP_SENDING は送出しない。ピアは RESET_STREAM のストリーム ID と
    アプリケーションエラーコードを観測し、追加のポンプでも STOP_SENDING を
    観測しない。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    stream_data_received = asyncio.Event()
    server_handshake_completed = asyncio.Event()

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_handshake_completed.set()

    async def on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None:
        stream_data_received.set()

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = _LowLevelPeer(server)
    try:
        await peer.handshake(lambda: server_handshake_completed.is_set())

        # ピアが双方向ストリームを開いてデータを送る
        stream_id = peer.connection.open_stream(bidirectional=True)
        assert stream_id >= 0
        peer.connection.send_stream_data(stream_id, b"ping", False)
        await peer.pump_until(stream_data_received.is_set)

        # サーバーが送信側だけを中断する
        error_code = 7
        await server.reset_stream(peer.local_addr, stream_id, error_code=error_code)

        # ピアが RESET_STREAM を観測する
        events = await peer.pump_events(
            lambda received: _first_event(received, EventType.STREAM_RESET, stream_id) is not None
        )
        reset_event = _first_event(events, EventType.STREAM_RESET, stream_id)
        assert reset_event is not None, "RESET_STREAM が観測できるべき"
        assert reset_event.error_code == error_code

        # 追加のポンプでも STOP_SENDING は観測しない (片方だけ送る操作)
        extra_events = await peer.pump_steps(EXTRA_PUMP_STEPS)
        assert _first_event(extra_events, EventType.STOP_SENDING, stream_id) is None, (
            "reset_stream は STOP_SENDING を送出しないべき"
        )
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        peer.close()
        await server.stop()


@pytest.mark.asyncio
async def test_stop_sending_sends_only_stop_sending(test_certificates) -> None:
    """stop_sending が STOP_SENDING だけを送出することを確認する

    stop_sending はピアの送信側だけを止める QUIC フレーム層の操作であり、
    RESET_STREAM は送出しない。ピアは STOP_SENDING のストリーム ID と
    アプリケーションエラーコードを観測し、追加のポンプでも RESET_STREAM を
    観測しない (ピア自身の ngtcp2 が STOP_SENDING へ自動応答で送出する
    RESET_STREAM は受信側の観測対象ではない)。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    stream_data_received = asyncio.Event()
    server_handshake_completed = asyncio.Event()

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_handshake_completed.set()

    async def on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None:
        stream_data_received.set()

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = _LowLevelPeer(server)
    try:
        await peer.handshake(lambda: server_handshake_completed.is_set())

        # ピアが双方向ストリームを開いてデータを送る (ピアの送信側を開く)
        stream_id = peer.connection.open_stream(bidirectional=True)
        assert stream_id >= 0
        peer.connection.send_stream_data(stream_id, b"ping", False)
        await peer.pump_until(stream_data_received.is_set)

        # サーバーがピアの送信側の停止を要求する
        error_code = 9
        await server.stop_sending(peer.local_addr, stream_id, error_code=error_code)

        # ピアが STOP_SENDING を観測する
        events = await peer.pump_events(
            lambda received: _first_event(received, EventType.STOP_SENDING, stream_id) is not None
        )
        stop_sending_event = _first_event(events, EventType.STOP_SENDING, stream_id)
        assert stop_sending_event is not None, "STOP_SENDING が観測できるべき"
        assert stop_sending_event.error_code == error_code

        # 追加のポンプでも RESET_STREAM は観測しない (片方だけ送る操作)
        extra_events = await peer.pump_steps(EXTRA_PUMP_STEPS)
        assert _first_event(extra_events, EventType.STREAM_RESET, stream_id) is None, (
            "stop_sending は RESET_STREAM を送出しないべき"
        )
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        peer.close()
        await server.stop()


@pytest.mark.asyncio
async def test_shutdown_stream_uni_local_sends_only_reset_stream(test_certificates) -> None:
    """サーバー起動の単方向ストリームの shutdown が RESET_STREAM だけを送ることを確認する

    ローカル単方向ストリームでは ngtcp2_conn_shutdown_stream が write 側
    (RESET_STREAM) のみを shutdown し、STOP_SENDING は送出しない。ピアは
    RESET_STREAM を観測し、追加のポンプでも STOP_SENDING を観測しない
    (quic.Client.shutdown_stream と同じ意味であることの確認)。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server_handshake_completed = asyncio.Event()

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_handshake_completed.set()

    server.on_handshake_completed(on_handshake_completed)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = _LowLevelPeer(server)
    try:
        await peer.handshake(lambda: server_handshake_completed.is_set())

        # サーバー起動の単方向ストリームを開いてデータを送る (mod 4 == 3)
        stream_id = await server.open_stream(peer.local_addr, bidirectional=False)
        assert stream_id % 4 == 3
        await server.send_stream_data(peer.local_addr, stream_id, b"hello", fin=False)

        # ピアがストリームデータを観測してストリームを認識する
        await peer.pump_events(
            lambda received: _first_event(received, EventType.STREAM_DATA, stream_id) is not None
        )

        # サーバーがストリームを中断する
        error_code = 0x2C
        await server.shutdown_stream(peer.local_addr, stream_id, error_code=error_code)

        # ピアが RESET_STREAM を観測する
        events = await peer.pump_events(
            lambda received: _first_event(received, EventType.STREAM_RESET, stream_id) is not None
        )
        reset_event = _first_event(events, EventType.STREAM_RESET, stream_id)
        assert reset_event is not None, "RESET_STREAM が観測できるべき"
        assert reset_event.error_code == error_code

        # 追加のポンプでも STOP_SENDING は観測しない (ローカル単方向は write 側のみ)
        extra_events = await peer.pump_steps(EXTRA_PUMP_STEPS)
        assert _first_event(extra_events, EventType.STOP_SENDING, stream_id) is None, (
            "ローカル単方向ストリームの shutdown は STOP_SENDING を送出しないべき"
        )
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        peer.close()
        await server.stop()


@pytest.mark.asyncio
async def test_stream_ops_unregistered_addr_do_nothing(test_certificates) -> None:
    """未登録の addr へのストリーム操作が何もせず例外にもならないことを確認する

    3 つの API はいずれも `_connections` を引いて未登録なら戻るだけであり、
    送信も接続状態の変更も行わない。登録済みの接続が引き続き往復できることで、
    未登録アドレスへの呼び出しが既存接続へ影響しないことも確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server_handshake_completed = asyncio.Event()

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_handshake_completed.set()

    async def on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None:
        # 受信したデータをそのまま返す (往復の確認用)
        await server.send_stream_data(addr, stream_id, b"echo:" + data, fin=fin)

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = _LowLevelPeer(server)
    try:
        await peer.handshake(lambda: server_handshake_completed.is_set())

        # 未登録の addr (接続していないポート) への呼び出しは例外にならない
        unknown_addr = ("127.0.0.1", 1)
        await server.shutdown_stream(unknown_addr, 0, error_code=1)
        await server.reset_stream(unknown_addr, 0, error_code=1)
        await server.stop_sending(unknown_addr, 0, error_code=1)

        # 接続の登録は増えず、登録済みの接続だけが残る
        assert set(server._connections) == {peer.local_addr}

        # 登録済みクライアントの往復は継続する
        stream_id = peer.connection.open_stream(bidirectional=True)
        assert stream_id >= 0
        peer.connection.send_stream_data(stream_id, b"ping", True)
        events = await peer.pump_events(
            lambda received: _first_event(received, EventType.STREAM_DATA, stream_id) is not None
        )
        stream_data_event = _first_event(events, EventType.STREAM_DATA, stream_id)
        assert stream_data_event is not None, "ストリームデータが観測できるべき"
        assert stream_data_event.data == b"echo:ping"
        assert stream_data_event.fin is True
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        peer.close()
        await server.stop()


@pytest.mark.asyncio
async def test_on_stream_reset_fires_once_with_error_code(test_certificates) -> None:
    """ピアの RESET_STREAM が on_stream_reset で 1 回だけ通知されることを確認する

    ピアが `close_stream` でストリームを中断すると RESET_STREAM が届き、
    コールバックがストリーム ID・アプリケーションエラーコード・ピアアドレスを
    受け取る。ngtcp2 のイベントはストリームにつき 1 回だけ配送されるため、
    通知も 1 回である (追加のポンプでも重複しない)。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    stream_data_received = asyncio.Event()
    server_handshake_completed = asyncio.Event()
    reset_received = asyncio.Event()
    resets: list[tuple[int, int, tuple[str, int]]] = []

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_handshake_completed.set()

    async def on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None:
        stream_data_received.set()

    async def on_stream_reset(stream_id: int, error_code: int, addr: tuple[str, int]) -> None:
        resets.append((stream_id, error_code, addr))
        reset_received.set()

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)
    server.on_stream_reset(on_stream_reset)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = _LowLevelPeer(server)
    try:
        await peer.handshake(lambda: server_handshake_completed.is_set())

        # ピアが双方向ストリームを開いてデータを送り、サーバーへストリームを
        # 認識させる
        stream_id = peer.connection.open_stream(bidirectional=True)
        assert stream_id >= 0
        peer.connection.send_stream_data(stream_id, b"ping", False)
        await peer.pump_until(stream_data_received.is_set)

        # ピアがストリームを中断する (RESET_STREAM と STOP_SENDING を送出する)
        error_code = 0x30
        peer.connection.close_stream(stream_id, error_code)

        # サーバーのコールバックが発火する
        await peer.pump_until(reset_received.is_set)

        # 追加のポンプでも重複通知が無いことを確認する
        await peer.pump_steps(EXTRA_PUMP_STEPS)
        assert resets == [(stream_id, error_code, peer.local_addr)]
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        peer.close()
        await server.stop()


@pytest.mark.asyncio
async def test_on_stream_reset_not_registered_keeps_connection(test_certificates) -> None:
    """on_stream_reset 未登録でもピアの RESET_STREAM で接続が壊れないことを確認する

    未登録の場合は通知しない (イベントは配送時に捨てられる)。接続は生存し、
    別のストリームでの往復が継続できる。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server_handshake_completed = asyncio.Event()

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_handshake_completed.set()

    async def on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None:
        await server.send_stream_data(addr, stream_id, b"echo:" + data, fin=fin)

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = _LowLevelPeer(server)
    try:
        await peer.handshake(lambda: server_handshake_completed.is_set())

        # 1 本目のストリームを開いてデータを送り、ピアから中断する
        first_stream_id = peer.connection.open_stream(bidirectional=True)
        assert first_stream_id >= 0
        peer.connection.send_stream_data(first_stream_id, b"ping", False)
        await peer.pump_events(
            lambda received: (
                _first_event(received, EventType.STREAM_DATA, first_stream_id) is not None
            )
        )
        peer.connection.close_stream(first_stream_id, 0x30)

        # 2 本目のストリームでは往復が継続する (接続は生存している)
        second_stream_id = peer.connection.open_stream(bidirectional=True)
        assert second_stream_id != first_stream_id
        peer.connection.send_stream_data(second_stream_id, b"ping", True)
        events = await peer.pump_events(
            lambda received: (
                _first_event(received, EventType.STREAM_DATA, second_stream_id) is not None
            )
        )
        stream_data_event = _first_event(events, EventType.STREAM_DATA, second_stream_id)
        assert stream_data_event is not None, "別ストリームの往復が継続するべき"
        assert stream_data_event.data == b"echo:ping"
        assert stream_data_event.fin is True
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        peer.close()
        await server.stop()


@pytest.mark.asyncio
async def test_high_level_client_observes_server_shutdown_stream(test_certificates) -> None:
    """高レベル Client がサーバーの shutdown_stream を RESET_STREAM で観測することを確認する

    サーバーがクライアント起動の双方向ストリームを shutdown_stream すると、
    サーバーの RESET_STREAM が直接届き、クライアントの
    `wait_for_stream_reset` がアプリケーションエラーコードを返す。
    """
    error_code = 0x2A
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(
        stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]
    ) -> None:
        # 応答を送らずにストリームを中断する
        await server.shutdown_stream(addr, stream_id, error_code=error_code)

    server.on_stream_data(on_server_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

        stream_id = await client.open_stream(bidirectional=True)
        await client.send_stream_data(stream_id, b"ping", fin=False)

        # サーバーの RESET_STREAM が運ぶエラーコードを観測する
        code = await asyncio.wait_for(client.wait_for_stream_reset(stream_id), timeout=5.0)
        assert code == error_code
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_high_level_client_observes_server_stop_sending(test_certificates) -> None:
    """高レベル Client がサーバーの stop_sending を STOP_SENDING で観測することを確認する

    サーバーがクライアント起動の双方向ストリームへ stop_sending すると、
    クライアントの `on_stop_sending` がストリーム ID とアプリケーション
    エラーコードを受け取る。
    """
    error_code = 0x2B
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(
        stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]
    ) -> None:
        # 応答を送らずにピアの送信側の停止を要求する
        await server.stop_sending(addr, stream_id, error_code=error_code)

    server.on_stream_data(on_server_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    received: list[tuple[int, int]] = []
    stop_sending_received = asyncio.Event()

    async def on_client_stop_sending(stream_id: int, code: int) -> None:
        received.append((stream_id, code))
        stop_sending_received.set()

    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    client.on_stop_sending(on_client_stop_sending)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

        stream_id = await client.open_stream(bidirectional=True)
        await client.send_stream_data(stream_id, b"ping", fin=False)

        # サーバーの STOP_SENDING が届く
        await asyncio.wait_for(stop_sending_received.wait(), timeout=5.0)
        assert received == [(stream_id, error_code)]
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()
