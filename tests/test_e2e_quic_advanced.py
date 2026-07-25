"""QUIC 証明書検証 / 0-RTT / Connection Migration の e2e テスト"""

from __future__ import annotations

import asyncio
import logging

import pytest

logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_verify_peer_rejects_self_signed(test_certificates):
    """自己署名証明書を verify_peer=True で拒否することを確認する"""
    from webtransport.quic import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    # CA なし + 検証有効では自己署名を信頼できない
    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=True,
    )
    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is False, "自己署名証明書は検証失敗になるべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_verify_peer_with_ca_file(test_certificates):
    """ca_file にサーバー証明書を渡せば verify_peer=True で接続できることを確認する"""
    from webtransport.quic import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    # 自己署名を CA として明示指定する
    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=True,
        ca_file=test_certificates["certfile"],
    )
    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is True, "ca_file 指定時は検証に成功するべき"

    client_task = asyncio.create_task(client.run())
    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_custom_verify_callback_accept(test_certificates):
    """カスタム検証コールバックで許可できることを確認する"""
    from webtransport.quic import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    seen_certs: list[list[bytes]] = []

    def verify_callback(certs: list[bytes]) -> bool:
        # ピア証明書チェーンを受け取り、内容を記録して許可する
        seen_certs.append(certs)
        logger.info("カスタム検証: 証明書 %d 枚を許可する", len(certs))
        return True

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=True,
        verify_callback=verify_callback,
    )
    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is True, "カスタム検証で許可すれば接続できるべき"
    assert len(seen_certs) >= 1
    assert len(seen_certs[0]) >= 1
    assert len(seen_certs[0][0]) > 0

    client_task = asyncio.create_task(client.run())
    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_custom_verify_callback_reject(test_certificates):
    """カスタム検証コールバックで拒否できることを確認する"""
    from webtransport.quic import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    def verify_callback(certs: list[bytes]) -> bool:
        logger.info("カスタム検証: 証明書を拒否する")
        return False

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=True,
        verify_callback=verify_callback,
    )
    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is False, "カスタム検証で拒否すれば接続できないべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_session_ticket_and_0rtt(test_certificates):
    """初回接続で ticket を取得し、再接続で 0-RTT を試行できることを確認する"""
    from webtransport.quic import Client, Server

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    port = server.actual_port

    # 1 回目: フルハンドシェイクで ticket と 0-RTT TP を得る
    client1 = Client(host="127.0.0.1", port=port, verify_peer=False)
    tickets: list[bytes] = []

    async def on_ticket(ticket: bytes) -> None:
        tickets.append(ticket)
        logger.info("セッションチケットを受信: %d バイト", len(ticket))

    client1.on_session_ticket(on_ticket)
    assert await asyncio.wait_for(client1.connect(), timeout=5.0) is True

    client1_task = asyncio.create_task(client1.run())
    # NewSessionTicket 到着を待つ
    for _ in range(50):
        ticket = client1.export_session_ticket()
        if ticket:
            break
        await asyncio.sleep(0.05)
    else:
        ticket = client1.export_session_ticket()

    assert ticket, "セッションチケットを取得できるべき"
    early_tp = client1.export_0rtt_transport_params()
    assert early_tp, "0-RTT トランスポートパラメータを取得できるべき"

    client1_task.cancel()
    await asyncio.gather(client1_task, return_exceptions=True)
    await client1.close()

    # 2 回目: ticket + TP で early data を試行する
    client2 = Client(
        host="127.0.0.1",
        port=port,
        verify_peer=False,
        session_ticket=ticket,
        early_transport_params=early_tp,
        enable_early_data=True,
    )
    assert await asyncio.wait_for(client2.connect(), timeout=5.0) is True
    assert client2.was_early_data_attempted() is True, "0-RTT を試行しているべき"
    logger.info(
        "0-RTT 受理=%s 試行=%s",
        client2.is_early_data_accepted(),
        client2.was_early_data_attempted(),
    )

    # 受理されなくても試行できていれば完了条件を満たす
    # (サーバ設定やタイミングで拒否される場合がある)
    client2_task = asyncio.create_task(client2.run())
    client2_task.cancel()
    server_task.cancel()
    await asyncio.gather(client2_task, server_task, return_exceptions=True)
    await client2.close()
    await server.stop()


@pytest.mark.asyncio
async def test_connection_migration(test_certificates):
    """initiate_migration 後もストリーム通信が継続することを確認する"""
    from webtransport.quic import Client, Server

    server_received: list[bytes] = []
    client_received: list[bytes] = []
    server_got_first = asyncio.Event()
    client_got_reply = asyncio.Event()
    server_got_second = asyncio.Event()
    client_got_second_reply = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream(stream_id: int, data: bytes, fin: bool, addr: object) -> None:
        server_received.append(data)
        logger.info("サーバー受信: %s", data)
        if data == b"before-migrate":
            server_got_first.set()
            await server.send_stream_data(addr, stream_id, b"ack-before", fin=False)
        elif data == b"after-migrate":
            server_got_second.set()
            await server.send_stream_data(addr, stream_id, b"ack-after", fin=True)

    server.on_stream_data(on_server_stream)
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    async def on_client_stream(stream_id: int, data: bytes, fin: bool) -> None:
        client_received.append(data)
        logger.info("クライアント受信: %s", data)
        if data == b"ack-before":
            client_got_reply.set()
        elif data == b"ack-after":
            client_got_second_reply.set()

    client.on_stream_data(on_client_stream)
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    client_task = asyncio.create_task(client.run())
    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"before-migrate", fin=False)

    await asyncio.wait_for(server_got_first.wait(), timeout=5.0)
    await asyncio.wait_for(client_got_reply.wait(), timeout=5.0)

    # ローカルアドレスを変えてマイグレーションする
    migrated = await client.migrate()
    assert migrated is True, "マイグレーションを開始できるべき"
    logger.info("マイグレーションを開始した")

    # path validation と通信継続を待つ
    await asyncio.sleep(0.2)
    await client.send_stream_data(stream_id, b"after-migrate", fin=True)

    await asyncio.wait_for(server_got_second.wait(), timeout=5.0)
    await asyncio.wait_for(client_got_second_reply.wait(), timeout=5.0)

    assert b"before-migrate" in server_received
    assert b"after-migrate" in server_received
    assert b"ack-before" in client_received
    assert b"ack-after" in client_received

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    await client.close()
    await server.stop()
