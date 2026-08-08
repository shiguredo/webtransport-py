"""QUIC クライアントの recv_stream_data の e2e テスト

高レベル Client の recv_stream_data が FIN までデータを受信し (data, fin) を
返すことを検証する。複数チャンクの累積連結・呼び出し時点での完了済み即時
return・ゼロ長 FIN・idle / overall タイムアウト・接続終了からの TimeoutError
を対象とする。
"""

from __future__ import annotations

import asyncio

import pytest

from webtransport.quic import Client, Server


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
async def test_recv_stream_data_basic(test_certificates):
    """recv_stream_data が FIN までデータを受信し (data, fin) を返すことを確認する

    サーバーが 1 回の send_stream_data でデータと FIN を送り、クライアントの
    recv_stream_data が (data, True) を返すことを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        await server.send_stream_data(addr, stream_id, b"hello", fin=True)

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

    data, fin = await asyncio.wait_for(client.recv_stream_data(stream_id), timeout=5.0)
    assert data == b"hello"
    assert fin is True

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_multiple_chunks(test_certificates):
    """複数 STREAM_DATA イベントの累積連結を確認する

    サーバーがデータを複数チャンクに分けて送信し、FIN で完了する。
    recv_stream_data が全チャンクを連結したデータを返すことを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        # 複数チャンクに分けて送信し、最後に FIN を付ける
        await server.send_stream_data(addr, stream_id, b"chunk-1-", fin=False)
        await asyncio.sleep(0.05)
        await server.send_stream_data(addr, stream_id, b"chunk-2-", fin=False)
        await asyncio.sleep(0.05)
        await server.send_stream_data(addr, stream_id, b"chunk-3", fin=True)

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

    data, fin = await asyncio.wait_for(client.recv_stream_data(stream_id), timeout=5.0)
    assert data == b"chunk-1-chunk-2-chunk-3"
    assert fin is True

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_already_finished(test_certificates):
    """呼び出し時点で FIN 完了済みのストリームは即時 return することを確認する

    サーバーが先にデータと FIN を送り、クライアントの受信タスクがそれを
    処理してから recv_stream_data を呼ぶと、待機せず即座に (data, True) を
    返すことを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        await server.send_stream_data(addr, stream_id, b"early", fin=True)

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

    # 受信タスクがデータ処理を完了するまで待つ (FIN 完了を確認する)
    for _ in range(50):
        state = client._recv_states.get(stream_id)
        if state is not None and state.fin:
            break
        await asyncio.sleep(0.05)
    assert client._recv_states.get(stream_id) is not None
    assert client._recv_states[stream_id].fin is True

    # 呼び出し時点で FIN 完了済みなら即時 return する
    data, fin = await asyncio.wait_for(client.recv_stream_data(stream_id), timeout=5.0)
    assert data == b"early"
    assert fin is True

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_zero_length_fin(test_certificates):
    """ゼロ長 FIN (datalen=0, fin=True) を完了として扱うことを確認する

    サーバーがデータなしで FIN のみを送り、recv_stream_data が
    (b"", True) を返すことを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        await server.send_stream_data(addr, stream_id, b"", fin=True)

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

    data, fin = await asyncio.wait_for(client.recv_stream_data(stream_id), timeout=5.0)
    assert data == b""
    assert fin is True

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_connection_closed(test_certificates):
    """接続終了 (CONNECTION_CLOSED) からの TimeoutError を確認する

    待機中にサーバーが接続を閉じると、recv_stream_data が TimeoutError を
    raise して待機を終了することを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        # データを送らずに待つ (close はテスト側で行う)
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

    # recv_stream_data を待機させ、サーバーを停止して接続を閉じる
    recv_task = asyncio.create_task(client.recv_stream_data(stream_id))
    await asyncio.sleep(0.2)
    await server.stop()

    # 接続終了で recv_stream_data 自身が TimeoutError を raise することを
    # 確認する (外部 wait_for のタイムアウト由来と区別するため match で
    # 絞り込む)
    with pytest.raises(TimeoutError, match="connection closed"):
        await asyncio.wait_for(recv_task, timeout=5.0)

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()


@pytest.mark.asyncio
async def test_recv_stream_data_idle_timeout(test_certificates):
    """進捗が無いまま idle deadline に達した場合の TimeoutError を確認する

    サーバーがデータを送らずに待つため、recv_stream_data が timeout 秒で
    TimeoutError を raise することを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
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

    # idle deadline を短くしてタイムアウトを検出する
    with pytest.raises(TimeoutError, match="idle timeout"):
        await asyncio.wait_for(
            client.recv_stream_data(stream_id, timeout=0.5),
            timeout=5.0,
        )

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_overall_timeout(test_certificates):
    """overall_timeout に達した場合の TimeoutError を確認する

    サーバーがデータを送らずに待つため、overall_timeout を短く指定すると
    idle deadline より先に全体タイムアウトが発生することを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
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

    # overall_timeout を idle deadline より短くする
    with pytest.raises(TimeoutError, match="overall timeout"):
        await asyncio.wait_for(
            client.recv_stream_data(
                stream_id,
                timeout=5.0,
                overall_timeout=0.5,
            ),
            timeout=5.0,
        )

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_multiple_streams_concurrent(test_certificates):
    """複数ストリームの並行 recv_stream_data が互いに干渉しないことを確認する

    2 つのストリームを開いて並行に受信待機し、それぞれが自分のストリームの
    データだけを FIN まで受信できることを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        # ストリーム ID に応じて異なるデータを返す
        if stream_id == 0:
            await server.send_stream_data(addr, stream_id, b"first", fin=True)
        else:
            await server.send_stream_data(addr, stream_id, b"second", fin=True)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    stream1 = await client.open_stream(bidirectional=True)
    stream2 = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream1, b"ping", fin=True)
    await client.send_stream_data(stream2, b"ping", fin=True)

    data1, fin1 = await asyncio.wait_for(client.recv_stream_data(stream1), timeout=5.0)
    data2, fin2 = await asyncio.wait_for(client.recv_stream_data(stream2), timeout=5.0)
    assert data1 == b"first"
    assert data2 == b"second"
    assert fin1 is True
    assert fin2 is True

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_from_callback_raises_runtime_error(test_certificates):
    """コールバック内から recv_stream_data を呼ぶと RuntimeError になることを確認する

    コールバック内から呼び出すと受信処理が進まないため、RuntimeError を
    raise する設計。コールバック内で recv_stream_data を呼んだ結果が
    RuntimeError になることを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
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

    async def on_client_stream_data(stream_id, data, fin):
        nonlocal callback_error
        # コールバック内から recv_stream_data を呼ぶと RuntimeError になる
        try:
            await client.recv_stream_data(stream_id)
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
async def test_recv_stream_data_idle_deadline_extends(test_certificates):
    """進捗があるたびに idle deadline が延びることを確認する

    timeout を短く設定しても、進捗 (STREAM_DATA 受信) があるたびに idle
    deadline が延びるため、全体が timeout 秒を超えても受信を継続できることを
    確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        # 各チャンクを timeout より短い間隔で送りつつ、全体は timeout を超える
        await server.send_stream_data(addr, stream_id, b"a", fin=False)
        await asyncio.sleep(0.3)
        await server.send_stream_data(addr, stream_id, b"b", fin=False)
        await asyncio.sleep(0.3)
        await server.send_stream_data(addr, stream_id, b"c", fin=True)

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

    # 各チャンク間隔 (0.3s) より短い idle タイムアウト (0.5s) でも、
    # 進捗で期限が延びて全体 (0.6s 超) を受信できる
    data, fin = await asyncio.wait_for(
        client.recv_stream_data(stream_id, timeout=0.5),
        timeout=5.0,
    )
    assert data == b"abc"
    assert fin is True

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_negative_timeout(test_certificates):
    """0 以下の timeout / overall_timeout で ValueError になることを確認する

    タイムアウト値の検証が正しく機能することを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
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
        await client.recv_stream_data(stream_id, timeout=0)
    with pytest.raises(ValueError):
        await client.recv_stream_data(stream_id, timeout=-1)
    with pytest.raises(ValueError):
        await client.recv_stream_data(stream_id, overall_timeout=0)
    with pytest.raises(ValueError):
        await client.recv_stream_data(stream_id, overall_timeout=-1)

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_stream_reset_extends_idle(test_certificates):
    """STREAM_RESET 受信で idle deadline が延長されることを確認する

    サーバーが STREAM_RESET を送ると、進捗として idle deadline が 1 回延長
    される。idle タイムアウト (0.5s) を迎える前にリセットが届くため、最初の
    idle 期限を過ぎても待機が継続されることを確認する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        # 少し待ってから STREAM_RESET を送る (最初の idle 期限より前に届く)
        await asyncio.sleep(0.2)
        server._connections[addr].reset_stream(stream_id, error_code=42)

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

    # idle タイムアウト 0.5s で待機する。STREAM_RESET が進捗として idle
    # deadline を 1 回延長するため、最初の idle 期限 (0.5s) の後も待機が
    # 継続される (リセットは 0.2s 後に届くため、延長後の期限は約 0.7s)
    recv_task = asyncio.create_task(
        client.recv_stream_data(stream_id, timeout=0.5, overall_timeout=3.0)
    )
    await asyncio.sleep(0.6)
    assert not recv_task.done(), "STREAM_RESET の進捗で最初の idle 期限を乗り越えるべき"

    # その後は idle deadline が尽きて idle timeout になる
    with pytest.raises(TimeoutError, match="idle timeout"):
        await asyncio.wait_for(recv_task, timeout=5.0)

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_recv_stream_data_both_callback_and_recv(test_certificates):
    """コールバックと recv_stream_data の両方にデータが配信されることを確認する

    on_stream_data コールバックと recv_stream_data は独立に動作し、併用しても
    データは両方に配信される。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        await server.send_stream_data(addr, stream_id, b"shared", fin=True)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    callback_data = []
    callback_got = asyncio.Event()

    async def on_client_stream_data(stream_id, data, fin):
        callback_data.append(data)
        callback_got.set()

    client.on_stream_data(on_client_stream_data)

    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"ping", fin=True)

    # コールバックにも recv_stream_data にも同じデータが配信される
    data, fin = await asyncio.wait_for(client.recv_stream_data(stream_id), timeout=5.0)
    await asyncio.wait_for(callback_got.wait(), timeout=5.0)
    assert data == b"shared"
    assert fin is True
    assert callback_data == [b"shared"]

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()
