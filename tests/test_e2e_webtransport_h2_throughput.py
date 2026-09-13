"""WebTransport over HTTP/2 の大容量転送スループットテスト

`h2` の高レベル API は接続ごとの受信ループを持ち、受信のたびに固定 sleep を
挟む実装では 1 チャンクあたりの遅延が積み上がってスループット上限になる。
ここでは 32 MiB の片方向転送が現実的な時間で完了することを確認する。
"""

import asyncio
import time

import pytest

# 転送量 (64 MiB) と許容時間 (10 秒)。h2 は quic ほど極端ではないが、
# 1 チャンクあたりの固定 sleep で 32 MiB が 8.7 秒まで落ちるため、律速が
# 戻った場合に閾値を超えるよう 64 MiB で検証する。ローカル実測は 1 秒未満で
# あり、遅い CI ランナーでも余裕がある
TRANSFER_BYTES = 64 * 1024 * 1024
TRANSFER_TIMEOUT_SECONDS = 10.0

# 1 回の send_stream_data で渡すチャンクサイズ (h2 の上限は 1 MiB)
CHUNK_BYTES = 64 * 1024


@pytest.mark.asyncio
async def test_client_to_server_large_transfer_throughput(test_certificates):
    """64 MiB のクライアント → サーバー転送が 10 秒以内に完了することを確認"""
    from webtransport.h2 import Client, Server

    received = 0
    completed = asyncio.Event()
    session_ready = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_writer):
        session_ready.set()

    async def on_stream_data(stream_id, data, session_writer):
        nonlocal received
        received += len(data)
        if received >= TRANSFER_BYTES:
            completed.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
    await server.start()

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
        f"64 MiB の転送に {elapsed:.2f} 秒かかった (上限 {TRANSFER_TIMEOUT_SECONDS} 秒)"
    )

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_server_to_client_large_transfer_throughput(test_certificates):
    """64 MiB のサーバー → クライアント転送が 10 秒以内に完了することを確認

    クライアント側の受信ループも同じ粒度の問題を持つ。
    """
    from webtransport.h2 import Client, Server

    received = 0
    completed = asyncio.Event()
    session_ready = asyncio.Event()
    writers: list[object] = []

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_writer):
        writers.append(session_writer)
        session_ready.set()

    server.on_session_ready(on_session_ready)
    await server.start()

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
    writer = writers[0]

    stream_id = await writer.open_stream()
    assert stream_id >= 0

    chunk = b"y" * CHUNK_BYTES
    started = time.monotonic()
    remaining = TRANSFER_BYTES
    while remaining > 0:
        size = min(CHUNK_BYTES, remaining)
        remaining -= size
        await writer.send_stream_data(stream_id, chunk[:size], fin=(remaining == 0))

    await asyncio.wait_for(completed.wait(), timeout=60.0)
    elapsed = time.monotonic() - started
    assert received == TRANSFER_BYTES, "全データが届くべき"
    assert elapsed <= TRANSFER_TIMEOUT_SECONDS, (
        f"64 MiB の転送に {elapsed:.2f} 秒かかった (上限 {TRANSFER_TIMEOUT_SECONDS} 秒)"
    )

    client_task.cancel()
    await asyncio.gather(client_task, return_exceptions=True)
    await client.close()
    await server.stop()
