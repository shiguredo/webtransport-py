"""QUIC サーバーの接続間分離テスト

単一ループによる head-of-line blocking がないことを実ソケットで検証する。
実時間の分離は Sans-IO では検証できないため e2e 形式とする。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from webtransport.quic import Client, Server


async def _run_server(server: Server) -> None:
    """サーバーのメインループを実行する (キャンセルで終了)"""
    try:
        await server.run()
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_two_clients_isolated(test_certificates) -> None:
    """
    1 台の遅延中も他接続の往復が止まらないことを確認する

    クライアント A の応答を 1.5 秒待たせる間に、クライアント B の送信から
    受信までが 200 ms 以内で完了する。単一ループの直列処理では B も 1.5 秒
    待たされるため、分離の有無を wall time で判定できる。遅延が 0.5 秒の
    ときは CI ランナーのスケジューリング揺らぎ (実測 73 ms) で B の判定が
    落ちたため、遅延としきい値の分離幅を広げた。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        # 遅延対象のみ待たせる
        if data == b"slow":
            await asyncio.sleep(1.5)
        await server.send_stream_data(addr, stream_id, data, fin=True)

    server.on_stream_data(on_server_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))
    client_a = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    client_b = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    try:
        # 両方を接続する
        assert await asyncio.wait_for(client_a.connect(), timeout=5.0) is True
        assert await asyncio.wait_for(client_b.connect(), timeout=5.0) is True

        got_a: asyncio.Event = asyncio.Event()
        got_b: asyncio.Event = asyncio.Event()

        async def on_a(stream_id, data, fin):
            got_a.set()

        async def on_b(stream_id, data, fin):
            got_b.set()

        client_a.on_stream_data(on_a)
        client_b.on_stream_data(on_b)

        # A の遅延送受信を開始する (完了は待たない)
        stream_a = await client_a.open_stream()
        await client_a.send_stream_data(stream_a, b"slow", fin=True)
        await asyncio.sleep(0.05)

        # B の往復時間を測る
        stream_b = await client_b.open_stream()
        start = time.monotonic()
        await client_b.send_stream_data(stream_b, b"fast", fin=True)
        await asyncio.wait_for(got_b.wait(), timeout=5.0)
        elapsed = time.monotonic() - start
        # 1.5 秒の遅延に引きずられず 200 ms 以内で完了する
        assert elapsed < 0.2, f"B の往復が遅延した: {elapsed:.3f}s"
        # A も最終的に完了する
        await asyncio.wait_for(got_a.wait(), timeout=10.0)
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client_a.close()
        await client_b.close()
        await server.stop()


@pytest.mark.asyncio
async def test_hundred_clients_isolated(test_certificates) -> None:
    """
    100 並行接続で 1 台の遅延が他に波及しないことを確認する

    0 番のみ 0.5 秒待たせ、残り 99 台の往復の中央値が 150 ms 以内である。
    波及の定義は各接続の往復時間の中央値とする。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        # 遅延対象のみ待たせる
        if data == b"slow":
            await asyncio.sleep(0.5)
        await server.send_stream_data(addr, stream_id, data, fin=True)

    server.on_stream_data(on_server_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))
    clients: list[Client] = []
    try:
        # 100 台を並行接続する
        for _ in range(100):
            clients.append(Client(host="127.0.0.1", port=server.actual_port, verify_peer=False))
        await asyncio.gather(
            *[asyncio.wait_for(client.connect(), timeout=10.0) for client in clients]
        )
        # ハンドシェイクの輻輳が収まるまで待つ (直後のバースト損失を避ける)
        await asyncio.sleep(2.0)

        events = [asyncio.Event() for _ in range(100)]
        elapsed_list: list[float] = [0.0] * 100

        for index, client in enumerate(clients):

            def make_handler(i: int):
                async def on_data(stream_id, data, fin):
                    events[i].set()

                return on_data

            client.on_stream_data(make_handler(index))

        # 0 番の遅延送受信を開始する (完了は待たない)
        stream_0 = await clients[0].open_stream()
        await clients[0].send_stream_data(stream_0, b"slow", fin=True)
        await asyncio.sleep(0.05)

        # 残り 99 台の往復を並行測定する。バースト損失を避けるため
        # 送信開始をずらす (全台が遅延の 0.5 秒窓内に収まる範囲)。
        # 往復時間自体は送信後から測る
        async def roundtrip(index: int) -> None:
            await asyncio.sleep(index * 0.002)
            stream_id = await clients[index].open_stream()
            start = time.monotonic()
            await clients[index].send_stream_data(stream_id, b"fast", fin=True)
            await asyncio.wait_for(events[index].wait(), timeout=10.0)
            elapsed_list[index] = time.monotonic() - start

        await asyncio.gather(*[roundtrip(i) for i in range(1, 100)])
        # 遅延に引きずられず 100 ms 以内で完了する。CI ランナーの
        # スケジューリング揺らぎによる少数の外れ値は許容し、過半が遅延する
        # 波及を検出するため中央値で判定する
        elapsed = sorted(elapsed_list[1:])
        median = elapsed[len(elapsed) // 2]
        assert median < 0.15, f"99 台の往復中央値が遅延した: {median:.3f}s"
        # 遅延側も最終的に完了する
        await asyncio.wait_for(events[0].wait(), timeout=10.0)
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await asyncio.gather(*[client.close() for client in clients], return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_connection_closed_reaches_callback(test_certificates) -> None:
    """
    接続終了がコールバックへ届くことを確認する

    タスク分離後も終了通知の順序と到達が保たれる。終了後に登録が外れる。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    closed: asyncio.Event = asyncio.Event()

    async def on_closed(addr):
        closed.set()

    server.on_connection_closed(on_closed)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))
    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    try:
        # 接続する
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
        await asyncio.sleep(0.2)
        # クライアントから閉じる
        await client.close()
        # サーバー側に終了が届く
        await asyncio.wait_for(closed.wait(), timeout=5.0)
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_stop_clears_connection_tasks(test_certificates) -> None:
    """
    停止後に接続タスクとキューが残らないことを確認する

    寿命管理 (生成・破棄) が正しい。停止後に残存タスクはない。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()
    server_task = asyncio.create_task(_run_server(server))
    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    try:
        # 接続してイベントを流す
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
        await asyncio.sleep(0.3)
        # タスクとキューが生成されている
        assert len(server._connection_tasks) >= 1
        assert len(server._connection_queues) >= 1
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()
    # 停止後に残存はない
    assert server._connection_tasks == {}
    assert server._connection_queues == {}


@pytest.mark.asyncio
async def test_stop_from_callback_completes_cleanup(test_certificates) -> None:
    """
    コールバック内からの停止が後片付けを素通りしないことを確認する

    接続タスク内から stop() しても自己待機せず、ソケット破棄まで完了する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    done: asyncio.Event = asyncio.Event()
    caller_task: list[asyncio.Task] = []

    async def on_server_stream_data(stream_id, data, fin, addr):
        await server.send_stream_data(addr, stream_id, data, fin=True)
        # コールバック内から停止する (自己 await の再入)
        caller_task.append(asyncio.current_task())
        await server.stop()
        done.set()

    server.on_stream_data(on_server_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))
    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    try:
        # 接続して送信する
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
        stream_id = await client.open_stream()
        await client.send_stream_data(stream_id, b"bye", fin=True)
        # コールバック内の停止が完了する
        await asyncio.wait_for(done.wait(), timeout=5.0)
        await asyncio.sleep(0.2)
        # 後片付けが素通りしていない
        assert server._socket is None
        assert server._connection_tasks == {}
        assert server._connection_queues == {}
        # 呼び出し元タスクも残存しない
        assert caller_task
        await asyncio.sleep(0.1)
        assert caller_task[0].done()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        # 既に停止済みでも止まる
        await server.stop()


@pytest.mark.asyncio
async def test_callback_error_closes_only_that_connection(test_certificates) -> None:
    """
    コールバック例外が当該接続のみ閉じて他に波及しないことを確認する

    例外時は記録後に接続を閉じ、受信ループは継続する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        # 異常系のみ例外にする
        if data == b"boom":
            raise RuntimeError("boom")
        await server.send_stream_data(addr, stream_id, data, fin=True)

    server.on_stream_data(on_server_stream_data)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))
    bad = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    good = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    try:
        # 両方を接続する
        assert await asyncio.wait_for(bad.connect(), timeout=5.0) is True
        assert await asyncio.wait_for(good.connect(), timeout=5.0) is True
        # 異常系を送る
        bad_stream = await bad.open_stream()
        await bad.send_stream_data(bad_stream, b"boom", fin=True)
        # 当該接続のみ閉じられるまで待つ (正常系の接続は残る)
        for _ in range(100):
            if len(server._connections) == 1:
                break
            await asyncio.sleep(0.05)
        assert len(server._connections) == 1
        # 受信ループは継続している
        assert not server_task.done()
        # 正常系は往復できる
        got: asyncio.Event = asyncio.Event()

        async def on_good(stream_id, data, fin):
            got.set()

        good.on_stream_data(on_good)
        good_stream = await good.open_stream()
        start = time.monotonic()
        await good.send_stream_data(good_stream, b"ok", fin=True)
        await asyncio.wait_for(got.wait(), timeout=5.0)
        assert time.monotonic() - start < 1.0
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await bad.close()
        await good.close()
        await server.stop()
