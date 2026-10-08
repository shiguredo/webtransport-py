"""QUIC の接続終了 (CONNECTION_CLOSE) API のテスト

高レベル `quic.Server.close(addr, error_code, reason)` と
`quic.Client.close(error_code, reason)` が、終了コードと理由付きの
CONNECTION_CLOSE (RFC 9000 Section 19.19) を実ソケットでピアへ伝えることを
検証する。ピア側の観測は公開 API (`quic.Client.run()` の例外) と、サーバーが
保持する低レベル `quic.Connection` のプロパティで行う (モックなし)。

ハンドシェイク完了前の終了では ngtcp2 が終了コードを APPLICATION_ERROR に
置換して理由を落とす (RFC 9000 Section 10.2.3)。終了コードと理由が伝わること
を検証するテストは、この既知の制約のためハンドシェイク完了後に close する。
"""

import asyncio
import socket
from collections.abc import Callable

import pytest

from webtransport._common import recv_datagram
from webtransport.quic import Client, Config, Connection, Server
from webtransport.quic.exceptions import QuicApplicationError, QuicTransportErrorCode
from webtransport.webtransport_ext.quic import ConnectionErrorType

# 受信待ちの上限 (秒)。サーバーの受信ループは 0.1 秒間隔で回るため、
# これより短いと空振りが増える
RECV_TIMEOUT = 0.2
# 状態変化を待つポーリングの試行上限と間隔 (秒)
WAIT_ATTEMPTS = 100
WAIT_INTERVAL = 0.05


async def _run_server(server: Server) -> None:
    """サーバーのメインループを実行する (キャンセルで終了)

    Args:
        server: 実行するサーバー
    """
    try:
        await server.run()
    except asyncio.CancelledError:
        pass


async def _wait_until(done: Callable[[], bool], message: str) -> None:
    """条件が満たされるまで上限付きで待つ

    Args:
        done: 条件を判定する述語
        message: 満たされなかった場合のエラーメッセージ (日本語)
    """
    for _ in range(WAIT_ATTEMPTS):
        if done():
            return
        await asyncio.sleep(WAIT_INTERVAL)
    raise AssertionError(message)


@pytest.mark.asyncio
async def test_server_close_sends_application_close_with_reason(test_certificates) -> None:
    """サーバーの close が終了コードと理由付きの CONNECTION_CLOSE を送ることを確認する

    ハンドシェイク完了後に `quic.Server.close(addr, error_code, reason)` を呼ぶと、
    クライアントはアプリケーションエラーの CONNECTION_CLOSE を受信する。
    ピア側では `quic.Client.run()` が送出する `QuicApplicationError` の
    error_code / reason として観測できる。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server_handshake_completed = asyncio.Event()
    server_addrs: list[tuple[str, int]] = []

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_addrs.append(addr)
        server_handshake_completed.set()

    server.on_handshake_completed(on_handshake_completed)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    run_task: asyncio.Task[None] | None = None
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

        # サーバー側のハンドシェイク完了 (接続登録) を待つ
        await asyncio.wait_for(server_handshake_completed.wait(), timeout=5.0)
        addr = server_addrs[0]

        # ピア側は run() で接続終了を待つ
        run_task = asyncio.create_task(client.run())

        # サーバーが 1 接続だけを終了コードと理由付きで閉じる
        error_code = 0x1234
        reason = "server shutdown"
        await server.close(addr, error_code=error_code, reason=reason)

        # ピアは終了コードと理由をアプリケーションエラーとして観測する
        with pytest.raises(QuicApplicationError) as exc_info:
            await asyncio.wait_for(run_task, timeout=5.0)
        assert exc_info.value.error_code == error_code
        assert exc_info.value.reason == reason
        assert exc_info.value.error_code_type == "application"
    finally:
        if run_task is not None and not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_server_close_unregistered_addr_does_nothing(test_certificates) -> None:
    """未登録の addr へのサーバー close が何もせず例外にもならないことを確認する

    `_connections` に無いアドレスでは戻るだけで、送信も接続状態の変更も行わない。
    登録済みの接続は残り、往復も継続する。`close` と対になる 3 つの
    ストリーム操作の未登録アドレス扱いは
    test_e2e_quic_server_stream_ops.py が検証する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server_handshake_completed = asyncio.Event()
    server_addrs: list[tuple[str, int]] = []

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_addrs.append(addr)
        server_handshake_completed.set()

    async def on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None:
        # 受信したデータをそのまま返す (往復の確認用)
        await server.send_stream_data(addr, stream_id, b"echo:" + data, fin=fin)

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    received: list[bytes] = []
    stream_data_received = asyncio.Event()

    async def on_client_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        received.append(data)
        stream_data_received.set()

    client.on_stream_data(on_client_stream_data)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
        await asyncio.wait_for(server_handshake_completed.wait(), timeout=5.0)
        addr = server_addrs[0]

        # 未登録の addr (接続していないポート) への close は例外にならない
        await server.close(("127.0.0.1", 1), error_code=0x10, reason="unknown")

        # 接続の登録は残り、サーバーも動作を続ける
        assert set(server._connections) == {addr}
        assert server.is_running is True

        # 登録済みクライアントの往復は継続する
        stream_id = await client.open_stream(bidirectional=True)
        await client.send_stream_data(stream_id, b"ping", fin=True)
        await asyncio.wait_for(stream_data_received.wait(), timeout=5.0)
        assert received == [b"echo:ping"]
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_server_close_does_not_fire_on_connection_closed(test_certificates) -> None:
    """サーバーの close が on_connection_closed を発火させないことを確認する

    `quic.Server.close` はローカル起点の終了として扱う (`quic.Client.close()` と
    同じ契約)。ピアが CONNECTION_CLOSE を受信したことを確認したうえで、サーバー
    側のコールバックが発火しないことと、後始末が `run()` の回収経路だけで完了
    すること (サーバーが停止しないまま接続の登録が外れること) を確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server_handshake_completed = asyncio.Event()
    server_addrs: list[tuple[str, int]] = []
    server_closed_addrs: list[tuple[str, int]] = []

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_addrs.append(addr)
        server_handshake_completed.set()

    async def on_server_connection_closed(addr: tuple[str, int]) -> None:
        server_closed_addrs.append(addr)

    server.on_handshake_completed(on_handshake_completed)
    server.on_connection_closed(on_server_connection_closed)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    client_closed = asyncio.Event()

    async def on_client_connection_closed() -> None:
        client_closed.set()

    client.on_connection_closed(on_client_connection_closed)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
        await asyncio.wait_for(server_handshake_completed.wait(), timeout=5.0)
        addr = server_addrs[0]

        # サーバーが 1 接続だけを閉じる
        await server.close(addr, error_code=0x20, reason="local close")

        # ピアが CONNECTION_CLOSE を受信したことを確認する
        await asyncio.wait_for(client_closed.wait(), timeout=5.0)

        # 回収経路 (run() の is_closed 判定) が接続の登録を外す
        await _wait_until(
            lambda: addr not in server._connections,
            "ローカル close 後の接続が回収されるべき",
        )

        # ローカル起点の終了では on_connection_closed を発火させない
        await asyncio.sleep(0.3)
        assert server_closed_addrs == []

        # stop() を呼んでいないためサーバーは動作を続ける
        assert server.is_running is True
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_server_close_keeps_other_clients_communicating(test_certificates) -> None:
    """1 接続の close が他クライアントの通信を止めないことを確認する

    同じサーバーへ 2 台のクライアントを接続し、片方だけを `close` する。閉じた
    側は CONNECTION_CLOSE を受信し、残った側は往復を継続できる (`stop()` を
    呼ばずに 1 接続だけを閉じられる)。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server_handshake_completed = asyncio.Event()
    server_addrs: list[tuple[str, int]] = []

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_addrs.append(addr)
        if len(server_addrs) == 2:
            server_handshake_completed.set()

    async def on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None:
        await server.send_stream_data(addr, stream_id, b"echo:" + data, fin=fin)

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client_a = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    client_b = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    a_closed = asyncio.Event()
    b_received: list[bytes] = []
    b_received_event = asyncio.Event()

    async def on_a_connection_closed() -> None:
        a_closed.set()

    async def on_b_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        b_received.append(data)
        b_received_event.set()

    client_a.on_connection_closed(on_a_connection_closed)
    client_b.on_stream_data(on_b_stream_data)
    try:
        assert await asyncio.wait_for(client_a.connect(), timeout=5.0) is True
        assert await asyncio.wait_for(client_b.connect(), timeout=5.0) is True
        await asyncio.wait_for(server_handshake_completed.wait(), timeout=5.0)

        # クライアント A の接続だけを閉じる
        addr_a = server_addrs[0]
        await server.close(addr_a, error_code=0x30, reason="close only A")
        await asyncio.wait_for(a_closed.wait(), timeout=5.0)

        # クライアント B の往復は継続する
        stream_id = await client_b.open_stream(bidirectional=True)
        await client_b.send_stream_data(stream_id, b"ping", fin=True)
        await asyncio.wait_for(b_received_event.wait(), timeout=5.0)
        assert b_received == [b"echo:ping"]

        # 閉じた側だけが登録から外れる
        await _wait_until(
            lambda: addr_a not in server._connections,
            "close した接続が回収されるべき",
        )
        assert len(server._connections) == 1
        assert server.is_running is True
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client_a.close()
        await client_b.close()
        await server.stop()


@pytest.mark.asyncio
async def test_client_close_error_code_and_reason_observed_by_server(test_certificates) -> None:
    """Client.close の終了コードと理由がピアで観測できることを確認する

    ハンドシェイク完了後に `quic.Client.close(error_code, reason)` を呼ぶと、
    サーバーは CONNECTION_CLOSE を受信し、保持している低レベル `quic.Connection`
    の error_code / reason とアプリケーション種別として観測できる。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server_handshake_completed = asyncio.Event()
    server_addrs: list[tuple[str, int]] = []
    server_connections: dict[tuple[str, int], Connection] = {}
    server_closed = asyncio.Event()
    server_closed_addrs: list[tuple[str, int]] = []

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        # 終了後の観測に備えて低レベル接続を保持する (終了時は登録から外れる)
        server_addrs.append(addr)
        server_connections[addr] = server._connections[addr]
        server_handshake_completed.set()

    async def on_connection_closed(addr: tuple[str, int]) -> None:
        server_closed_addrs.append(addr)
        server_closed.set()

    server.on_handshake_completed(on_handshake_completed)
    server.on_connection_closed(on_connection_closed)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
        await asyncio.wait_for(server_handshake_completed.wait(), timeout=5.0)
        addr = server_addrs[0]

        # 終了コードと理由を指定して閉じる (戻り値は従来どおり送出パケット数)
        error_code = 0x99
        reason = "client done"
        assert await client.close(error_code=error_code, reason=reason) == 1

        # サーバーはピアの CONNECTION_CLOSE を受信する
        await asyncio.wait_for(server_closed.wait(), timeout=5.0)
        assert server_closed_addrs == [addr]

        connection = server_connections[addr]
        assert connection.error_code == error_code
        assert connection.reason == reason
        assert connection.error_code_type is ConnectionErrorType.APPLICATION
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_client_close_default_arguments_keep_previous_behavior(test_certificates) -> None:
    """引数省略時の Client.close が従来どおり動作することを確認する

    引数なしの `close()` は終了コード 0・空の理由で CONNECTION_CLOSE を送出し、
    送出パケット数 1 を返す。ピアは CONNECTION_CLOSE を受信して DRAINING 状態に
    なる。ccerr の error_code 0 は「アプリケーションエラーなし」を意味し、
    ngtcp2 の受信状態では未受信と区別できないため、低レベル接続の error_code /
    reason は None になる。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server_handshake_completed = asyncio.Event()
    server_addrs: list[tuple[str, int]] = []
    server_connections: dict[tuple[str, int], Connection] = {}
    server_closed = asyncio.Event()

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        server_addrs.append(addr)
        server_connections[addr] = server._connections[addr]
        server_handshake_completed.set()

    async def on_connection_closed(addr: tuple[str, int]) -> None:
        server_closed.set()

    server.on_handshake_completed(on_handshake_completed)
    server.on_connection_closed(on_connection_closed)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    try:
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
        await asyncio.wait_for(server_handshake_completed.wait(), timeout=5.0)
        addr = server_addrs[0]

        # 引数なしの close は従来どおり 1 パケット (CONNECTION_CLOSE) を送出する
        assert await client.close() == 1

        # サーバーは CONNECTION_CLOSE を受信して DRAINING 状態になる
        await asyncio.wait_for(server_closed.wait(), timeout=5.0)
        connection = server_connections[addr]
        assert connection.in_draining_period is True
        assert connection.error_code is None
        assert connection.reason is None
        assert connection.error_code_type is None
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_server_close_before_handshake_replaces_error_code(test_certificates) -> None:
    """ハンドシェイク完了前の close では終了コードが置換され理由が落ちることを確認する

    RFC 9000 Section 10.2.3 により、ハンドシェイク完了前に送る CONNECTION_CLOSE
    は Initial / Handshake パケットでしか送れないため、ngtcp2 が終了コードを
    APPLICATION_ERROR (0x0c) に置換し理由を落とす。この既知の制約のため、
    終了コードと理由の観測はハンドシェイク完了後に close する他のテストで
    検証する。ここでは制約そのものを検証する。

    ピアは実 UDP ソケットで駆動する低レベル接続とし、クライアント Initial の
    みを送ってハンドシェイクを完了させない (以降は送信せず受信だけを行う)。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    server_addr: tuple[str, int] = ("127.0.0.1", server.actual_port)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.bind(("127.0.0.1", 0))
    # getsockname() は IPv4 かつ 127.0.0.1 bind のため (str, int) になる
    local_addr: tuple[str, int] = sock.getsockname()

    config = Config()
    config.alpn_protocols = ["h3"]
    config.verify_peer = False
    config.server_name = "localhost"
    peer = Connection.create_client(config, local_addr, server_addr)
    try:
        # クライアント Initial だけを送り、ハンドシェイクは完了させない
        initial_packet = peer.send()
        assert initial_packet is not None
        sock.sendto(initial_packet.data, server_addr)

        # サーバーが接続を受理して登録するまで待つ
        await _wait_until(
            lambda: local_addr in server._connections,
            "サーバーが接続を受理するべき",
        )
        assert peer.is_handshake_completed() is False

        # ハンドシェイク完了前に終了コードと理由を指定して閉じる
        await server.close(local_addr, error_code=0x100, reason="mid handshake close")

        # ピアは Initial パケットの CONNECTION_CLOSE を受信して DRAINING になる
        for _ in range(100):
            result = await recv_datagram(sock, RECV_TIMEOUT)
            if result is None:
                continue
            data, _ = result
            peer.receive(data, local_addr, server_addr)
            if peer.is_closed():
                break
        assert peer.is_closed() is True, "CONNECTION_CLOSE を受信するべき"

        # 終了コードは APPLICATION_ERROR に置換され、理由は落ちる
        assert peer.error_code == QuicTransportErrorCode.APPLICATION_ERROR
        assert peer.reason == ""
        assert peer.in_draining_period is True
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        sock.close()
        await server.stop()
