"""QUIC クライアントのバックグラウンド受信タスクの e2e テスト

connect() がバックグラウンド受信タスクを起動するため、run() を明示起動し
なくても受信イベントが処理される。受信タスクの停止・マイグレーション後の
継続・異常終了時の connect() への伝播を検証する。
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
async def test_receive_without_run(test_certificates):
    """connect() 後に run() なしで受信イベントが処理されコールバックが発火することを確認する

    バックグラウンド受信タスクが STREAM_DATA イベントを処理してコールバック
    を発火するため、run() を起動しなくてもサーバーからのデータを受信できる。
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

    client_received = []
    client_got_data = asyncio.Event()

    async def on_client_stream_data(stream_id, data, fin):
        client_received.append(data)
        client_got_data.set()

    client.on_stream_data(on_client_stream_data)

    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is True

    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"ping", fin=True)

    # run() を起動せずにサーバーからの応答を受信できる
    await asyncio.wait_for(client_got_data.wait(), timeout=5.0)
    assert client_received == [b"pong"]

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_close_stops_background_task(test_certificates):
    """close() でバックグラウンド受信タスクが停止することを確認する

    close() は終了フラグを落としてタスクの完了を待ち、タスクは異常終了と
    して扱われずに正常終了する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    # 受信タスクが起動していることを確認する
    assert client._recv_task is not None
    assert not client._recv_task.done()

    await client.close()

    # close() で受信タスクが正常終了 (異常終了扱いではない) することを確認する
    assert client._recv_task is not None
    assert client._recv_task.done()
    assert client._recv_task.exception() is None

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await server.stop()


@pytest.mark.asyncio
async def test_migrate_continues_receive(test_certificates):
    """migrate() 後もバックグラウンド受信タスクが受信を継続することを確認する

    ソケット差し替え後も受信タスクは self._socket を再参照して受信を継続し、
    マイグレーション前後のデータをコールバックで受け取れる。クライアント側
    の受信がマイグレーション後も継続することを、応答の受信回数で検証する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    server_received = []
    client_got_after_migrate = asyncio.Event()

    async def on_server_stream_data(stream_id, data, fin, addr):
        server_received.append(data)
        # 1 通目は fin=False で応答し、書き込み側を開いたままにする
        # (2 通目の応答を同一ストリームで送るため)
        await server.send_stream_data(addr, stream_id, b"ack", fin=False)

    server.on_stream_data(on_server_stream_data)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    client_received = []

    async def on_client_stream_data(stream_id, data, fin):
        client_received.append(data)
        # マイグレーション後の応答 (2 通目の ack) を受信したことを記録する
        if client_received.count(b"ack") == 2:
            client_got_after_migrate.set()

    client.on_stream_data(on_client_stream_data)

    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is True

    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"before-migrate", fin=False)

    # マイグレーション前に 1 通目の応答を受信できる
    for _ in range(50):
        if client_received.count(b"ack") == 1:
            break
        await asyncio.sleep(0.05)
    assert client_received.count(b"ack") == 1, "マイグレーション前の応答を受信できるべき"

    migrated = await client.migrate()
    assert migrated is True

    # パス検証と通信継続を待つ (マイグレーション後は新アドレスからの
    # 送信がサービスされるまでに時間を要する)
    await asyncio.sleep(0.2)

    # マイグレーション後もクライアント側の受信が継続することを確認する
    await client.send_stream_data(stream_id, b"after-migrate", fin=True)
    await asyncio.wait_for(client_got_after_migrate.wait(), timeout=5.0)
    assert b"after-migrate" in server_received, "マイグレーション後の送信が届くべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_close_from_callback_does_not_deadlock(test_certificates):
    """コールバック内から close() を呼んでもデッドロックしないことを確認する

    コールバックはバックグラウンド受信タスク内で await されるため、close()
    がタスク自身の完了を待つとデッドロックする。再入ガードによりタスク内
    からの close() は完了待ちをスキップし、タスクは正常終了する。
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

    async def on_client_stream_data(stream_id, data, fin):
        # コールバック内から close() を呼ぶ (デッドロックしないこと)
        await client.close()

    client.on_stream_data(on_client_stream_data)

    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is True

    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"ping", fin=True)

    # close() がコールバック内から呼ばれ、タスクが停止するまでポーリングで待つ
    for _ in range(100):
        if client._recv_task is not None and client._recv_task.done():
            break
        await asyncio.sleep(0.05)
    assert client._recv_task is not None
    assert client._recv_task.done()
    assert client._recv_task.exception() is None

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await server.stop()


@pytest.mark.asyncio
async def test_close_from_subtask_does_not_deadlock(test_certificates):
    """コールバックが起動したサブタスク内から close() を呼んでもデッドロックしないことを確認する

    コールバック内で起動したサブタスクから close() を呼ぶと、asyncio.current_task()
    は受信タスクではなくサブタスクになる。_in_callback フラグにより受信タスクの
    コールバック実行中と判定し、close() の完了待ちをスキップしてデッドロックを
    回避する。
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

    async def on_client_stream_data(stream_id, data, fin):
        # サブタスクを起動してその中で close() を呼ぶ
        async def close_in_subtask():
            await client.close()

        await asyncio.create_task(close_in_subtask())

    client.on_stream_data(on_client_stream_data)

    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is True

    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"ping", fin=True)

    # close() がサブタスク内から呼ばれ、タスクが停止するまでポーリングで待つ
    for _ in range(100):
        if client._recv_task is not None and client._recv_task.done():
            break
        await asyncio.sleep(0.05)
    assert client._recv_task is not None
    assert client._recv_task.done()
    assert client._recv_task.exception() is None

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await server.stop()


@pytest.mark.asyncio
async def test_task_error_propagates_to_connect(test_certificates):
    """バックグラウンド受信タスクの異常終了が connect() の待機者へ伝播することを確認する

    コールバック内で raise された例外はタスクの異常終了となり、connect() の
    待機者へ元の例外が伝播される。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    async def on_handshake_completed():
        raise ValueError("handshake callback failed")

    client.on_handshake_completed(on_handshake_completed)

    # connect() の待機者へ元の例外が伝播する
    with pytest.raises(ValueError, match="handshake callback failed"):
        await asyncio.wait_for(client.connect(), timeout=5.0)

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)

    # タスクが保持した例外が connect() へ伝播されていることを確認する
    assert client._recv_task is not None
    assert client._recv_task.done()
    assert isinstance(client._recv_task.exception(), ValueError)

    await client.close()
    await server.stop()
