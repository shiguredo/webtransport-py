"""HTTP/3 の大容量レスポンススループットテスト

`http3` の高レベル API は `h3` と同じく受信ループを 1 パケットずつ回し、
固定 sleep を挟む実装では 1 パケットあたりの遅延がスループット上限になる。
ここでは 32 MiB のレスポンスが現実的な時間で完了することを確認する。
"""

import asyncio
import time

import pytest

# 転送量 (32 MiB) と許容時間 (10 秒)。ローカル実測は 1 秒未満であり、
# 遅い CI ランナーでも余裕がある
TRANSFER_BYTES = 32 * 1024 * 1024
TRANSFER_TIMEOUT_SECONDS = 10.0

# 1 回の send_data で渡すチャンクサイズ
CHUNK_BYTES = 64 * 1024


@pytest.mark.asyncio
async def test_large_response_throughput(test_certificates):
    """32 MiB のレスポンスが 10 秒以内に完了することを確認"""
    from webtransport.http3 import Client, Server

    received = 0
    completed = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        await server.submit_response(addr, stream_id, [(":status", "200")])
        chunk = b"x" * CHUNK_BYTES
        remaining = TRANSFER_BYTES
        while remaining > 0:
            size = min(CHUNK_BYTES, remaining)
            remaining -= size
            await server.send_data(addr, stream_id, chunk[:size], fin=(remaining == 0))

    server.on_request(on_request)
    await server.start()

    server_task = asyncio.create_task(server.run())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    async def on_data(stream_id, data):
        nonlocal received
        received += len(data)
        if received >= TRANSFER_BYTES:
            completed.set()

    client.on_data(on_data)
    await client.connect()

    stream_id = await client.request("GET", "/large")
    assert stream_id >= 0
    await client.send_data(stream_id, b"", fin=True)

    client_task = asyncio.create_task(client.run())

    started = time.monotonic()
    await asyncio.wait_for(completed.wait(), timeout=60.0)
    elapsed = time.monotonic() - started
    assert received == TRANSFER_BYTES, "全データが届くべき"
    assert elapsed <= TRANSFER_TIMEOUT_SECONDS, (
        f"32 MiB の転送に {elapsed:.2f} 秒かかった (上限 {TRANSFER_TIMEOUT_SECONDS} 秒)"
    )

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    await client.close()
    await server.stop()
