"""QUIC クライアントの shutdown_stream / wait_for_stream_reset のテスト

高レベル Client の shutdown_stream と wait_for_stream_reset を検証する。
エラーコードの複製・タイムアウト・接続終了・接続生存・ローカル単方向
ストリームの分岐を対象とする。クライアントが送出する RESET_STREAM は
高レベル Server が STREAM_RESET を処理しないため、Sans-IO 実通信テストで
ピア側の STREAM_RESET イベント受信を確認する。
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import CLIENT_ADDR, SERVER_ADDR, create_client_server_pair, perform_handshake

from webtransport.quic import Client, EventType, Server


async def _run_server(server: Server) -> None:
    """サーバーのメインループを実行する (キャンセルで終了)

    Args:
        server: 実行するサーバー
    """
    try:
        await server.run()
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_shutdown_stream_error_code_replication(test_certificates):
    """shutdown_stream のエラーコードがピアの RESET_STREAM で複製されることを確認する

    クライアントが shutdown_stream を呼ぶと STOP_SENDING を送出し、ngtcp2 が
    自動で RESET_STREAM を返す (RFC 9000 Section 3.5 の MUST。エラーコードは
    STOP_SENDING から複製する SHOULD)。wait_for_stream_reset がそのエラー
    コードを返すことを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]):
        # サーバーの送信側を Ready/Send に留める (fin=False でエコー)。
        # fin=True でエコーすると全 ACK 到達後に STOP_SENDING への自動応答が
        # 出なくなる (RFC 9000 Section 3.5 の Data Sent 状態の MAY)
        await server.send_stream_data(addr, stream_id, b"echo", fin=False)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"ping", fin=False)

    # shutdown_stream で STOP_SENDING を送出し、エラーコードを指定する
    await client.shutdown_stream(stream_id, error_code=42)

    # ピアの自動応答 RESET_STREAM が運ぶエラーコードを取得できる
    code = await asyncio.wait_for(client.wait_for_stream_reset(stream_id), timeout=5.0)
    assert code == 42

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_wait_for_stream_reset_timeout(test_certificates):
    """完結済みストリームへの shutdown_stream では RESET_STREAM が送出されずタイムアウトすることを確認する

    エコー往復 (send_stream_data(..., fin=True) → recv_stream_data) でストリームを
    完結済み (write 側全 ACK / read 側 FIN 受信済み) にしてから shutdown_stream を
    呼ぶ。書き込み側が既に完了しているため RESET_STREAM は送出されず、ピアの
    自動応答も無いため wait_for_stream_reset がタイムアウトする。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]):
        await server.send_stream_data(addr, stream_id, b"echo", fin=True)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"ping", fin=True)

    # エコーを完結済み (FIN 付き) で受信し、ストリームを完結させる
    data, fin = await asyncio.wait_for(client.recv_stream_data(stream_id), timeout=5.0)
    assert data == b"echo"
    assert fin is True

    # 完結済みストリームへの shutdown_stream は RESET_STREAM を送出しない
    await client.shutdown_stream(stream_id, error_code=42)

    # ピアの自動応答も無いためタイムアウトする
    with pytest.raises(TimeoutError, match="timeout while waiting for stream reset"):
        await asyncio.wait_for(
            client.wait_for_stream_reset(stream_id, timeout=0.5),
            timeout=5.0,
        )

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_wait_for_stream_reset_connection_closed(test_certificates):
    """接続終了 (CONNECTION_CLOSED) からの TimeoutError を確認する

    待機中にサーバーが接続を閉じると、wait_for_stream_reset が TimeoutError
    を raise して待機を終了する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]):
        # 応答を送らずに待つ
        await asyncio.sleep(0)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"ping", fin=True)

    # 待機中にサーバーを停止して接続を閉じる
    wait_task = asyncio.create_task(client.wait_for_stream_reset(stream_id))
    await asyncio.sleep(0.2)
    await server.stop()

    with pytest.raises(TimeoutError, match="connection closed"):
        await asyncio.wait_for(wait_task, timeout=5.0)

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()


@pytest.mark.asyncio
async def test_stream_abort_connection_survives(test_certificates):
    """中断したストリームとは別のストリームでデータ転送が継続できることを確認する"""
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]):
        await server.send_stream_data(addr, stream_id, b"echo", fin=fin)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    # ストリーム 1 を中断する
    stream1 = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream1, b"ping", fin=False)
    await client.shutdown_stream(stream1, error_code=1)

    # ストリーム 2 でデータ転送が継続できる
    stream2 = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream2, b"ping", fin=True)
    data, fin = await asyncio.wait_for(client.recv_stream_data(stream2), timeout=5.0)
    assert data == b"echo"
    assert fin is True

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_wait_for_stream_reset_already_received(test_certificates):
    """呼び出し時点で既に RESET_STREAM を受信済みのストリームは即時 return することを確認する"""
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]):
        # 受信したストリームをサーバー側からリセットする (RESET_STREAM 送出)
        server._connections[addr].reset_stream(stream_id, 7)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"ping", fin=False)

    # サーバーの reset_stream が届き、受信状態にエラーコードが記録されるまで待つ
    for _ in range(50):
        state = client._recv_states.get(stream_id)
        if state is not None and state.reset_error_code is not None:
            break
        await asyncio.sleep(0.05)
    assert client._recv_states[stream_id].reset_error_code is not None

    # 受信済みなので即時 return する
    code = await asyncio.wait_for(client.wait_for_stream_reset(stream_id), timeout=5.0)
    assert code == 7

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_shutdown_stream_uni_local(test_certificates):
    """ローカル単方向ストリームの shutdown は RESET_STREAM のみ送出することを確認する

    ローカル単方向ストリームでは ngtcp2_conn_shutdown_stream が write 側
    (RESET_STREAM) のみを shutdown し、STOP_SENDING は送出しない。ピアは
    STOP_SENDING を受信しないため自動応答も無く、wait_for_stream_reset は
    タイムアウトする。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]):
        # 単方向ストリームのデータは受信するが応答しない
        await asyncio.sleep(0)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    # ローカル単方向ストリーム (クライアント発 = mod 4 == 2)
    stream_id = await client.open_stream(bidirectional=False)
    assert stream_id % 4 == 2
    await client.send_stream_data(stream_id, b"hello", fin=False)

    await client.shutdown_stream(stream_id, error_code=42)

    # STOP_SENDING が送出されないため自動応答が無く、タイムアウトする
    with pytest.raises(TimeoutError, match="timeout while waiting for stream reset"):
        await asyncio.wait_for(
            client.wait_for_stream_reset(stream_id, timeout=0.5),
            timeout=5.0,
        )

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_keeps_idle_after_reset(test_certificates):
    """STREAM_RESET 受信時も recv_stream_data の挙動が維持されることを確認する

    recv_stream_data 待機中に STREAM_RESET を受信すると、STREAM_RESET は
    進捗として扱われ idle deadline を 1 回延長し、その後は idle timeout になる。
    wait_for_stream_reset はエラーコードを返し、recv_stream_data の挙動は
    変化しない。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]):
        # 少し待ってからリセットする (最初の idle 期限より前に届く)
        await asyncio.sleep(0.2)
        server._connections[addr].reset_stream(stream_id, 9)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"ping", fin=False)

    # recv_stream_data は STREAM_RESET を進捗として扱うため、idle deadline が
    # 延長されて idle timeout になる (overall_timeout が先に来る場合はそちら)
    recv_task = asyncio.create_task(
        client.recv_stream_data(stream_id, timeout=0.5, overall_timeout=3.0)
    )
    # STREAM_RESET の進捗で最初の idle 期限 (0.5s) を乗り越える
    await asyncio.sleep(0.6)
    assert not recv_task.done(), "STREAM_RESET の進捗で最初の idle 期限を乗り越えるべき"

    with pytest.raises(TimeoutError, match="idle timeout"):
        await asyncio.wait_for(recv_task, timeout=5.0)

    # wait_for_stream_reset はエラーコードを返す
    code = await asyncio.wait_for(client.wait_for_stream_reset(stream_id), timeout=5.0)
    assert code == 9

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_wait_for_stream_reset_negative_timeout(test_certificates):
    """0 以下の timeout で ValueError になることを確認する"""
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]):
        # 応答を送らずに待つ
        await asyncio.sleep(0)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    stream_id = await client.open_stream(bidirectional=True)

    # 0 以下のタイムアウト値は ValueError になる
    with pytest.raises(ValueError):
        await client.wait_for_stream_reset(stream_id, timeout=0)
    with pytest.raises(ValueError):
        await client.wait_for_stream_reset(stream_id, timeout=-1)

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_wait_for_stream_reset_from_callback_raises_runtime_error(test_certificates):
    """コールバック内から wait_for_stream_reset を呼ぶと RuntimeError になることを確認する

    コールバック内から呼び出すと受信処理が進まないため、RuntimeError を
    raise する設計。on_stream_data コールバック内で呼んだ結果が RuntimeError
    になることを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]):
        await server.send_stream_data(addr, stream_id, b"pong", fin=True)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    callback_error = None

    async def on_client_stream_data(stream_id: int, data: bytes, fin: bool):
        nonlocal callback_error
        # コールバック内から wait_for_stream_reset を呼ぶと RuntimeError になる
        try:
            await client.wait_for_stream_reset(stream_id)
        except RuntimeError as exc:
            callback_error = exc

    client.on_stream_data(on_client_stream_data)

    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"ping", fin=True)

    # コールバックが発火して RuntimeError が記録されるまで待つ
    for _ in range(50):
        if callback_error is not None:
            break
        await asyncio.sleep(0.05)
    assert callback_error is not None
    assert isinstance(callback_error, RuntimeError)

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_shutdown_stream_from_callback(test_certificates):
    """コールバック内から shutdown_stream を呼べることを確認する

    shutdown_stream はフレームのスケジュールと送出のみで受信タスクの完了を
    待たないため、コールバック内から呼んでもデッドロックしない。
    wait_for_stream_reset と異なり RuntimeError にはならない。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]):
        await server.send_stream_data(addr, stream_id, b"pong", fin=True)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    shutdown_error = None

    async def on_client_stream_data(stream_id: int, data: bytes, fin: bool):
        nonlocal shutdown_error
        # コールバック内から shutdown_stream を呼ぶ (デッドロックしない)
        try:
            await client.shutdown_stream(stream_id, error_code=3)
        except RuntimeError as exc:
            shutdown_error = exc

    client.on_stream_data(on_client_stream_data)

    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"ping", fin=True)

    # コールバックが発火し、shutdown_stream が RuntimeError なしで完了するまで待つ
    for _ in range(50):
        if shutdown_error is not None:
            break
        await asyncio.sleep(0.05)
    assert shutdown_error is None, "shutdown_stream はコールバック内から呼べるべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


def test_close_stream_sends_reset_sans_io():
    """close_stream (shutdown_stream の送出元) がピアへ RESET_STREAM を送出することを確認する

    高レベル shutdown_stream は低レベル close_stream を呼んでフレームを
    スケジュールし、_send_pending() で送出する。高レベル Server は
    STREAM_RESET を処理しないため、Sans-IO 実通信テストで低レベル Connection
    同士のパケット交換を行い、ピア側の STREAM_RESET イベント受信を確認する。
    エラーコードの往復による高レベル API の検証は
    test_shutdown_stream_error_code_replication が担う。
    """

    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # クライアントがストリームを開いて送信し、サーバーに届ける
    stream_id = client.open_stream(bidirectional=True)
    client.send_stream_data(stream_id, b"ping", fin=False)
    for _ in range(20):
        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

    # shutdown_stream の送出元である close_stream を呼び、RESET_STREAM を送出する
    client.close_stream(stream_id, 42)
    for _ in range(20):
        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

    # ピア側で STREAM_RESET イベントを受信している
    saw_reset = False
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == EventType.STREAM_RESET:
            saw_reset = True
            assert event.stream_id == stream_id
            assert event.error_code == 42
    assert saw_reset, "close_stream で RESET_STREAM が送出されるべき"
