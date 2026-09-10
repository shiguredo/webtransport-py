"""QUIC サーバーの DCID ルーティングテスト

未知アドレスからの short header による既存接続の張り替え (接続ハイジャック
相当) を防ぐことと、正当な Migration の受付を検証する (実ソケットを使う。
モックなし)。サーバーの接続管理は内部状態で直接確認する
"""

from __future__ import annotations

import asyncio
import os
import socket

import pytest

from webtransport.quic import Config, Connection, Server


async def _handshake_over_socket(
    client: Connection,
    sock: socket.socket,
    server_addr: tuple[str, int],
) -> None:
    """低レベル接続のハンドシェイクを実ソケットで完了させる"""
    # Initial を送出する
    packet = client.send()
    assert packet is not None
    sock.sendto(packet.data, server_addr)

    # 応答を交換してハンドシェイクを完了させる
    loop = asyncio.get_running_loop()
    local_addr = sock.getsockname()
    for _ in range(100):
        try:
            data, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 65535), timeout=1.0)
        except TimeoutError:
            break
        client.receive(data, local_addr, server_addr)
        if client.is_handshake_completed():
            break
        packet = client.send()
        if packet is not None:
            sock.sendto(packet.data, server_addr)
    assert client.is_handshake_completed()


async def _settle(
    client: Connection,
    sock: socket.socket,
    server_addr: tuple[str, int],
) -> None:
    """ハンドシェイク残余フライトがなくなるまで送受信する"""
    loop = asyncio.get_running_loop()
    for _ in range(30):
        drained = False
        for _ in range(20):
            try:
                data, _ = await asyncio.wait_for(loop.sock_recvfrom(sock, 65535), timeout=0.05)
            except TimeoutError:
                break
            client.receive(data, sock.getsockname(), server_addr)
            drained = True
        packet = client.send()
        if packet is not None:
            sock.sendto(packet.data, server_addr)
        if not drained and packet is None:
            return
    raise AssertionError("静寂化しませんでした")


@pytest.mark.asyncio
async def test_spoofed_short_header_keeps_keys(test_certificates) -> None:
    """未知アドレスからの short header 1 発で既存接続が変わらない"""
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()
    server_task = asyncio.create_task(server.run())
    try:
        # 低レベルクライアントでハンドシェイクする
        client_config = Config()
        client_config.alpn_protocols = ["h3"]
        client_config.verify_peer = False
        client_config.server_name = "localhost"
        server_addr = ("127.0.0.1", server.actual_port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(("127.0.0.1", 0))
        try:
            client = Connection.create_client(client_config, sock.getsockname(), server_addr)
            await _handshake_over_socket(client, sock, server_addr)
            await asyncio.sleep(0.5)

            # 接続が 1 件だけ登録されている
            assert set(server._connections) == {sock.getsockname()}

            # 攻撃者ソケットから乱数 short header を送る
            attacker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            attacker.setblocking(False)
            attacker.bind(("127.0.0.1", 0))
            try:
                for _ in range(3):
                    attacker.sendto(b"\x40" + os.urandom(40), server_addr)
                await asyncio.sleep(0.5)

                # 既存接続のアドレスキーは変わらない
                assert set(server._connections) == {sock.getsockname()}
            finally:
                attacker.close()

            # 正規 DCID を付けた復号不能パケットでも張り替わらない
            connection = server._connections[sock.getsockname()]
            dcid = connection.scid[0]
            attacker2 = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            attacker2.setblocking(False)
            attacker2.bind(("127.0.0.1", 0))
            try:
                for _ in range(3):
                    attacker2.sendto(b"\x40" + dcid + os.urandom(32), server_addr)
                await asyncio.sleep(0.5)

                # 破棄判定のため張り替わらない
                assert set(server._connections) == {sock.getsockname()}
            finally:
                attacker2.close()
        finally:
            sock.close()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_nat_rebinding_keeps_connection(test_certificates) -> None:
    """受信元アドレス変化で既存接続が保持される (正当な Migration)"""
    received: list[tuple[int, bytes]] = []

    async def on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None:
        received.append((stream_id, data))

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    server.on_stream_data(on_stream_data)
    await server.start()
    server_task = asyncio.create_task(server.run())
    try:
        # 低レベルクライアントでハンドシェイクする
        client_config = Config()
        client_config.alpn_protocols = ["h3"]
        client_config.verify_peer = False
        client_config.server_name = "localhost"
        server_addr = ("127.0.0.1", server.actual_port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(("127.0.0.1", 0))
        try:
            client = Connection.create_client(client_config, sock.getsockname(), server_addr)
            await _handshake_over_socket(client, sock, server_addr)
            old_addr = sock.getsockname()
            # サーバー側が接続を登録するまで上限付きで待つ (固定 sleep では
            # 低速な CI ランナーで不足する)
            for _ in range(100):
                if set(server._connections) == {old_addr}:
                    break
                await asyncio.sleep(0.1)
            assert set(server._connections) == {old_addr}

            # 残余フライトを流して 1-RTT のみにする
            await _settle(client, sock, server_addr)

            # ストリームを開いてデータを積み、short header パケットだけ集める
            stream_id = client.open_stream(True)
            assert stream_id >= 0
            client.send_stream_data(stream_id, b"migrated-hello", False)
            short_packets = []
            for _ in range(20):
                packet = client.send()
                if packet is None:
                    break
                if packet.data and (packet.data[0] & 0x80) == 0:
                    short_packets.append(packet.data)
            assert short_packets

            # 新規ソケット (別ポート) から正規パケットを送る
            # (NAT リバインドの模擬)
            migrated = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            migrated.setblocking(False)
            migrated.bind(("127.0.0.1", 0))
            try:
                new_addr = migrated.getsockname()
                for payload in short_packets:
                    migrated.sendto(payload, server_addr)

                # DCID 一致かつ受理のためアドレスキーが張り替わるまで
                # 上限付きで待つ (固定 sleep では低速な CI ランナーで不足する)
                for _ in range(100):
                    if set(server._connections) == {new_addr}:
                        break
                    await asyncio.sleep(0.1)

                # DCID 一致かつ受理のためアドレスキーが張り替わる
                assert set(server._connections) == {new_addr}
                # ストリームデータが届いている
                assert (stream_id, b"migrated-hello") in received
            finally:
                migrated.close()
        finally:
            sock.close()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_spray_does_not_touch_connections(test_certificates) -> None:
    """未知アドレスの spray が既存接続に触れない (O(1) 照会)"""
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()
    server_task = asyncio.create_task(server.run())
    try:
        # 2 件のクライアントを接続する
        server_addr = ("127.0.0.1", server.actual_port)
        sockets = []
        try:
            for _ in range(2):
                client_config = Config()
                client_config.alpn_protocols = ["h3"]
                client_config.verify_peer = False
                client_config.server_name = "localhost"
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                sock.bind(("127.0.0.1", 0))
                sockets.append(sock)
                client = Connection.create_client(client_config, sock.getsockname(), server_addr)
                await _handshake_over_socket(client, sock, server_addr)
            await asyncio.sleep(0.5)
            assert len(server._connections) == 2

            # 各接続のカウンタを記録する
            counters = {
                addr: (connection.pkt_recv, connection.pkt_discarded)
                for addr, connection in server._connections.items()
            }

            # 未知アドレスから乱数 short header を spray する
            attacker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            attacker.setblocking(False)
            attacker.bind(("127.0.0.1", 0))
            try:
                for _ in range(10):
                    attacker.sendto(b"\x40" + os.urandom(40), server_addr)
                await asyncio.sleep(0.5)

                # どの接続にも触れていない (試し受信の走査が無い)
                for addr, connection in server._connections.items():
                    assert (connection.pkt_recv, connection.pkt_discarded) == (counters[addr])
                assert set(server._connections) == set(counters)
            finally:
                attacker.close()
        finally:
            for sock in sockets:
                sock.close()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()


@pytest.mark.asyncio
async def test_idle_timeout_notifies_and_removes(test_certificates) -> None:
    """アイドルタイムアウトで即時通知され登録が外れる"""
    closed: list[tuple[str, int]] = []

    async def on_closed(addr: tuple[str, int]) -> None:
        closed.append(addr)

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
        idle_timeout_ns=1_000_000_000,
    )
    server.on_connection_closed(on_closed)
    await server.start()
    server_task = asyncio.create_task(server.run())
    try:
        # 低レベルクライアントでハンドシェイクする
        client_config = Config()
        client_config.alpn_protocols = ["h3"]
        client_config.verify_peer = False
        client_config.server_name = "localhost"
        server_addr = ("127.0.0.1", server.actual_port)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        sock.bind(("127.0.0.1", 0))
        try:
            client = Connection.create_client(client_config, sock.getsockname(), server_addr)
            await _handshake_over_socket(client, sock, server_addr)
            await asyncio.sleep(0.5)
            client_addr = sock.getsockname()
            assert set(server._connections) == {client_addr}

            # アイドルタイムアウト後に終了が通知され登録が外れる
            await asyncio.sleep(3.0)
            assert closed == [client_addr]
            assert server._connections == {}
        finally:
            sock.close()
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()
