"""WebTransport over HTTP/3 の大容量転送スループットテスト

`h3` の高レベル API は `quic` とは別に受信ループを持つ。1 回のウェイク
アップで 1 パケットしか読まず、固定 sleep を挟む実装では、1 パケット
あたりのループ オーバーヘッドがそのままスループット上限になる (実測で
4 MiB に 64 秒)。受信したパケットの処理が遅れると ngtcp2 の RTT 計測が
過大になり、pacing と PTO も過大になってさらに遅くなる。

ここでは 32 MiB の片方向転送が現実的な時間で完了することを確認する。
"""

import asyncio
import time

import pytest

# 転送量 (32 MiB) と許容時間 (10 秒)。ローカル実測は 1 秒未満であり、
# 遅い CI ランナーでも余裕がある
TRANSFER_BYTES = 32 * 1024 * 1024
TRANSFER_TIMEOUT_SECONDS = 10.0

# 1 回の send_stream_data で渡すチャンクサイズ
CHUNK_BYTES = 64 * 1024


@pytest.mark.asyncio
async def test_client_to_server_large_transfer_throughput(test_certificates):
    """32 MiB のクライアント → サーバー転送が 10 秒以内に完了することを確認"""
    from webtransport.h3 import Client, Server

    received = 0
    completed = asyncio.Event()
    session_ready = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session_ready.set()

    async def on_stream_data(session_id, stream_id, data, addr):
        nonlocal received
        received += len(data)
        if received >= TRANSFER_BYTES:
            completed.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
    await server.start()

    server_task = asyncio.create_task(server.run())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/throughput",
        verify_peer=False,
    )
    await client.connect()
    client_task = asyncio.create_task(client.run())

    await asyncio.wait_for(session_ready.wait(), timeout=5.0)

    stream_id = await client.open_stream()
    assert stream_id >= 0

    chunk = b"x" * CHUNK_BYTES
    started = time.monotonic()
    remaining = TRANSFER_BYTES
    while remaining > 0:
        size = min(CHUNK_BYTES, remaining)
        remaining -= size
        await client.send_stream_data(stream_id, chunk[:size], fin=(remaining == 0))

    await asyncio.wait_for(completed.wait(), timeout=60.0)
    elapsed = time.monotonic() - started
    assert received == TRANSFER_BYTES, "全データが届くべき"
    assert elapsed <= TRANSFER_TIMEOUT_SECONDS, (
        f"32 MiB の転送に {elapsed:.2f} 秒かかった (上限 {TRANSFER_TIMEOUT_SECONDS} 秒)"
    )

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    # サーバーを先に止める。ピアの CONNECT ストリーム終了を待つ
    # close_wait_timeout (既定 3 秒) を待たずに済む
    await server.stop()
    await client.close()


@pytest.mark.asyncio
async def test_server_to_client_large_transfer_throughput(test_certificates):
    """32 MiB のサーバー → クライアント転送が 10 秒以内に完了することを確認

    クライアント側の受信ループも同じ粒度の問題を持つ。受信のたびに固定
    sleep を重ねると RTT が過大に評価され、pacing と PTO も過大になって
    転送が著しく遅くなる。
    """
    from webtransport.h3 import Client, Server

    received = 0
    completed = asyncio.Event()
    session_ready = asyncio.Event()
    session: list[tuple[tuple[str, int], int]] = []

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        session.append((addr, session_id))
        session_ready.set()

    server.on_session_ready(on_session_ready)
    await server.start()

    server_task = asyncio.create_task(server.run())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/throughput-reverse",
        verify_peer=False,
    )
    await client.connect()
    client_task = asyncio.create_task(client.run())

    async def on_client_stream_data(stream_id, data):
        nonlocal received
        received += len(data)
        if received >= TRANSFER_BYTES:
            completed.set()

    client.on_stream_data(on_client_stream_data)

    await asyncio.wait_for(session_ready.wait(), timeout=5.0)
    addr, session_id = session[0]

    stream_id = await server.open_stream(addr, session_id)
    assert stream_id >= 0

    chunk = b"y" * CHUNK_BYTES
    started = time.monotonic()
    remaining = TRANSFER_BYTES
    while remaining > 0:
        size = min(CHUNK_BYTES, remaining)
        remaining -= size
        await server.send_stream_data(addr, stream_id, chunk[:size], fin=(remaining == 0))

    await asyncio.wait_for(completed.wait(), timeout=60.0)
    elapsed = time.monotonic() - started
    assert received == TRANSFER_BYTES, "全データが届くべき"
    assert elapsed <= TRANSFER_TIMEOUT_SECONDS, (
        f"32 MiB の転送に {elapsed:.2f} 秒かかった (上限 {TRANSFER_TIMEOUT_SECONDS} 秒)"
    )

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    # サーバーを先に止める。ピアの CONNECT ストリーム終了を待つ
    # close_wait_timeout (既定 3 秒) を待たずに済む
    await server.stop()
    await client.close()
