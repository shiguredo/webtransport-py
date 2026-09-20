"""高レベル層の `connect()` 再入ガードのテスト

`close()` を挟まずに接続を確立したインスタンスへ `connect()` を再度呼ぶと
`RuntimeError` になること、`connect()` の実行中に再度呼んでも `RuntimeError` に
なること、`close()` の後は `quic.Client` 以外の 4 層で再度接続できることを
5 層それぞれで検証する。実際のサーバーとソケットを使い、モックは使わない。
"""

import asyncio
import contextlib
import socket

import pytest

from webtransport import h2, h3, http2, http3, quic
from webtransport.exceptions import WebTransportConnectError

# 5 層のサーバーのいずれか (起動と停止だけを共通化する)
_Server = quic.Server | h3.Server | http3.Server | h2.Server | http2.Server


async def _start_server(server: _Server) -> asyncio.Task[None]:
    """サーバーを起動して受信ループのタスクを返す"""
    await server.start()

    async def run_server() -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await server.run()

    return asyncio.create_task(run_server())


async def _stop_server(server: _Server, task: asyncio.Task[None]) -> None:
    """サーバーと受信ループのタスクを停止する"""
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await server.stop()


def _silent_udp_socket() -> tuple[socket.socket, int]:
    """応答しない UDP ソケットを用意する (接続中の状態を作るため)"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    return sock, sock.getsockname()[1]


async def _silent_tcp_server() -> tuple[asyncio.Server, int]:
    """接続は受け付けるが応答しない TCP サーバーを用意する (接続中の状態を作るため)"""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # 応答せずに切断まで待つ (クライアントの connect() は応答を待ち続ける)
        with contextlib.suppress(asyncio.CancelledError):
            await reader.read(-1)
        writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


@pytest.mark.asyncio
async def test_quic_connect_reentry(test_certificates) -> None:
    """quic.Client の connect() 再入が RuntimeError になることを確認する

    quic 層は 1 インスタンス 1 回の契約であり、close() の後も再接続できない
    (再試行には新しい Client が必要)。
    """
    server = quic.Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    task = await _start_server(server)
    try:
        client = quic.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
        assert await client.connect() is True
        with pytest.raises(RuntimeError):
            await client.connect()

        await client.close()
        # close() を挟んでも 1 インスタンス 1 回の契約は変わらない
        with pytest.raises(RuntimeError):
            await client.connect()
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_h3_connect_reentry(test_certificates) -> None:
    """h3.Client の connect() 再入が RuntimeError になり、close() 後は再接続できることを確認する"""
    server = h3.Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    task = await _start_server(server)
    url = f"https://127.0.0.1:{server.actual_port}/webtransport"
    try:
        client = h3.Client(url=url, verify_peer=False)
        await client.connect()
        with pytest.raises(RuntimeError):
            await client.connect()

        await client.close()
        # close() の後の再接続は契約として許容されている
        await client.connect()
        await client.close()
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_http3_connect_reentry(test_certificates) -> None:
    """http3.Client の connect() 再入が RuntimeError になり、close() 後は再接続できることを確認する"""
    server = http3.Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    task = await _start_server(server)
    try:
        client = http3.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
        await client.connect()
        # 確立済みのインスタンスへの再入は拒否される
        with pytest.raises(RuntimeError):
            await client.connect()

        await client.close()
        await client.connect()
        await client.close()
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_h2_connect_reentry(test_certificates) -> None:
    """h2.Client の connect() 再入が RuntimeError になり、close() 後は再接続できることを確認する"""
    server = h2.Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    task = await _start_server(server)
    url = f"https://127.0.0.1:{server.actual_port}/webtransport"
    try:
        client = h2.Client(url=url, verify_peer=False)
        await client.connect()
        # 確立済みのインスタンスへの再入は拒否される
        with pytest.raises(RuntimeError):
            await client.connect()

        await client.close()
        await client.connect()
        await client.close()
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_http2_connect_reentry(test_certificates) -> None:
    """http2.Client の connect() 再入が RuntimeError になり、close() 後は再接続できることを確認する"""
    server = http2.Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    task = await _start_server(server)
    try:
        client = http2.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
        assert await client.connect() is True
        # 確立済みのインスタンスへの再入は拒否される
        with pytest.raises(RuntimeError):
            await client.connect()

        await client.close()
        assert await client.connect() is True
        await client.close()
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_connect_in_progress_reentry() -> None:
    """connect() の実行中に再度 connect() を呼ぶと 5 層すべてで RuntimeError になることを確認する

    応答しないソケットをピアにして connect() を実行中の状態を作る。実行中の
    再入は接続確立後と同じく拒否される。
    """
    # quic / h3 / http3 は応答しない UDP ソケットをピアにする
    udp_socket, udp_port = _silent_udp_socket()
    try:
        udp_clients = [
            quic.Client(host="127.0.0.1", port=udp_port, verify_peer=False),
            h3.Client(url=f"https://127.0.0.1:{udp_port}/webtransport", verify_peer=False),
            http3.Client(host="127.0.0.1", port=udp_port, verify_peer=False),
        ]
        for client in udp_clients:
            task = asyncio.create_task(client.connect(timeout=0.5))
            await asyncio.sleep(0.05)
            with pytest.raises(RuntimeError):
                await client.connect(timeout=0.5)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            # キャンセルでも実行中フラグと transport を残さない
            # (残ると再入が恒久的に拒否される)
            assert client._connecting is False, "キャンセル後に実行中フラグが残っている"
            # transport の破棄。
            # quic 層は 1 インスタンス 1 回の契約であり close() が後始末する
            if not isinstance(client, quic.Client):
                assert client._socket is None, "キャンセル後にソケットが残っている"
            await client.close()
    finally:
        udp_socket.close()

    # h2 / http2 は応答しない TCP サーバーをピアにする
    tcp_server, tcp_port = await _silent_tcp_server()
    try:
        tcp_clients = [
            h2.Client(url=f"https://127.0.0.1:{tcp_port}/webtransport", verify_peer=False),
            http2.Client(host="127.0.0.1", port=tcp_port, verify_peer=False),
        ]
        for client in tcp_clients:
            task = asyncio.create_task(client.connect())
            await asyncio.sleep(0.1)
            with pytest.raises(RuntimeError):
                await client.connect()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            # キャンセルでも transport を残さない (残ると再入が拒否される)
            assert client._writer is None, "キャンセル後に writer が残っている"
            await client.close()
    finally:
        tcp_server.close()
        await tcp_server.wait_closed()


def _reserve_port(sock_type: int) -> int:
    """未使用のポート番号を 1 つ確保する (サーバー起動前の接続失敗を作るため)"""
    sock = socket.socket(socket.AF_INET, sock_type)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.mark.asyncio
async def test_h3_connect_retry_after_failure(test_certificates) -> None:
    """h3.Client が connect() 失敗後に同じインスタンスで再試行できることを確認する

    失敗の後始末 (`_abandon_attempt`) が状態を戻すため、再入ガードに塞がれずに
    再試行できる。サーバーが起動していないポートへの接続は
    ConnectTimeoutError で失敗する。
    """
    port = _reserve_port(socket.SOCK_DGRAM)
    client = h3.Client(url=f"https://127.0.0.1:{port}/webtransport", verify_peer=False)
    with pytest.raises(WebTransportConnectError):
        await client.connect(timeout=0.3)

    server = h3.Server(
        host="127.0.0.1",
        port=port,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    task = await _start_server(server)
    try:
        # 同じインスタンスで再試行できる
        await client.connect(timeout=3.0)
        await client.close()
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_http3_connect_retry_after_failure(test_certificates) -> None:
    """http3.Client が connect() 失敗後に同じインスタンスで再試行できることを確認する"""
    port = _reserve_port(socket.SOCK_DGRAM)
    client = http3.Client(host="127.0.0.1", port=port, verify_peer=False)
    with pytest.raises(WebTransportConnectError):
        await client.connect(timeout=0.3)

    server = http3.Server(
        host="127.0.0.1",
        port=port,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    task = await _start_server(server)
    try:
        await client.connect(timeout=3.0)
        await client.close()
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_h2_connect_retry_after_failure(test_certificates) -> None:
    """h2.Client が connect() 失敗後に同じインスタンスで再試行できることを確認する

    サーバーが起動していないポートへの TCP 接続は接続拒否で失敗する。
    """
    port = _reserve_port(socket.SOCK_STREAM)
    client = h2.Client(url=f"https://127.0.0.1:{port}/webtransport", verify_peer=False)
    with pytest.raises(WebTransportConnectError):
        await client.connect(timeout=1.0)

    server = h2.Server(
        host="127.0.0.1",
        port=port,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    task = await _start_server(server)
    try:
        await client.connect(timeout=3.0)
        await client.close()
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
async def test_http2_connect_retry_after_failure(test_certificates) -> None:
    """http2.Client が connect() 失敗後に同じインスタンスで再試行できることを確認する"""
    port = _reserve_port(socket.SOCK_STREAM)
    client = http2.Client(host="127.0.0.1", port=port, verify_peer=False)
    with pytest.raises(OSError):
        await client.connect()

    server = http2.Server(
        host="127.0.0.1",
        port=port,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    task = await _start_server(server)
    try:
        assert await client.connect() is True
        await client.close()
    finally:
        await _stop_server(server, task)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "layer",
    ["h2", "http2"],
    ids=["h2", "http2"],
)
async def test_transport_remaining_rejects_reentry(layer: str, test_certificates) -> None:
    """transport だけが残った状態 (run() がセッション終了を観測した後) でも再入が拒否されることを確認する

    `Client._connected` が False でも `StreamWriter` が残っている状態を再現し、
    再入が拒否されること (前回の transport を閉じないまま上書きしないこと) を
    確認する。`close()` で transport を破棄した後は再入できる。
    """
    if layer == "h2":
        server = h2.Server(
            host="127.0.0.1",
            port=0,
            certfile=test_certificates["certfile"],
            keyfile=test_certificates["keyfile"],
        )
    else:
        server = http2.Server(
            host="127.0.0.1",
            port=0,
            certfile=test_certificates["certfile"],
            keyfile=test_certificates["keyfile"],
        )
    task = await _start_server(server)
    try:
        if layer == "h2":
            client = h2.Client(
                url=f"https://127.0.0.1:{server.actual_port}/webtransport",
                verify_peer=False,
            )
        else:
            client = http2.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
        await client.connect()

        # run() がセッション終了を観測した状態 (transport は残る) を再現する
        client._connected = False
        with pytest.raises(RuntimeError):
            await client.connect()

        await client.close()
        # close() が transport を破棄した後は再入できる
        await client.connect()
        await client.close()
    finally:
        await _stop_server(server, task)
