"""webtransport.quic の大容量転送スループットテスト

受信ループが 1 回のウェイクアップで 1 パケットしか読まず、固定 sleep を
挟む実装では、1 パケットあたりのループ オーバーヘッドがそのまま
スループット上限になる (実測で 1 MiB/s 程度)。ストリーム受信フロー制御の
再開放 (MAX_STREAM_DATA) も同じループで送出されるため、遅延すると送信側の
ウィンドウが閉じたままになりさらに遅くなる。

ここでは 32 MiB の片方向転送が現実的な時間で完了することを確認する。
閾値は「32 MiB を 10 秒以内」とし、律速が戻った場合に検出できるようにする。
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


async def _run_transfer(
    test_certificates,
    *,
    server_to_client: bool,
) -> float:
    """32 MiB の片方向転送を行い、完了までの秒数を返す

    Args:
        test_certificates: テスト用の証明書
        server_to_client: True ならサーバー → クライアント、False なら
            クライアント → サーバーの方向で転送する

    Returns:
        転送開始から全量到達までの秒数
    """
    from webtransport.quic import Client, Server

    received = 0
    completed = asyncio.Event()
    chunk = b"x" * CHUNK_BYTES

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream_data(stream_id, data, fin, addr):
        nonlocal received
        received += len(data)
        if received >= TRANSFER_BYTES:
            completed.set()

    async def on_server_stream_data_for_reverse(stream_id, data, fin, addr):
        # クライアントが送った 1 バイトを合図に、同じ双方向ストリームへ
        # サーバーから全量を送る
        remaining = TRANSFER_BYTES
        while remaining > 0:
            size = min(CHUNK_BYTES, remaining)
            remaining -= size
            await server.send_stream_data(addr, stream_id, chunk[:size], fin=(remaining == 0))

    server.on_stream_data(
        on_server_stream_data_for_reverse if server_to_client else on_server_stream_data
    )
    await server.start()

    server_task = asyncio.create_task(server.run())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
    client_task = asyncio.create_task(client.run())

    async def on_client_stream_data(stream_id, data, fin):
        nonlocal received
        received += len(data)
        if received >= TRANSFER_BYTES:
            completed.set()

    client.on_stream_data(on_client_stream_data)

    stream_id = await client.open_stream(bidirectional=True)
    assert stream_id >= 0

    started = time.monotonic()
    if server_to_client:
        # サーバーはストリームの存在を知らないため、1 バイトだけ送って合図する
        await client.send_stream_data(stream_id, b"s")
    else:
        remaining = TRANSFER_BYTES
        while remaining > 0:
            size = min(CHUNK_BYTES, remaining)
            remaining -= size
            await client.send_stream_data(stream_id, chunk[:size], fin=(remaining == 0))

    await asyncio.wait_for(completed.wait(), timeout=60.0)
    elapsed = time.monotonic() - started
    assert received == TRANSFER_BYTES, "全データが届くべき"

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    await client.close()
    await server.stop()
    return elapsed


@pytest.mark.asyncio
async def test_client_to_server_large_transfer_throughput(test_certificates):
    """32 MiB のクライアント → サーバー転送が 10 秒以内に完了することを確認

    受信ループの粒度が 1 パケットに固定されると 1 MiB/s 程度まで落ちる。
    複数パケットのまとめ取りと期限ベースの待機でこれが解消していることを
    回帰検出する。
    """
    elapsed = await _run_transfer(test_certificates, server_to_client=False)
    assert elapsed <= TRANSFER_TIMEOUT_SECONDS, (
        f"32 MiB の転送に {elapsed:.2f} 秒かかった (上限 {TRANSFER_TIMEOUT_SECONDS} 秒)"
    )


@pytest.mark.asyncio
async def test_server_to_client_large_transfer_throughput(test_certificates):
    """32 MiB のサーバー → クライアント転送が 10 秒以内に完了することを確認

    クライアント側の受信ループも同じ粒度の問題を持つ。受信のたびに
    QUIC のタイマー期限ぶんの sleep を重ねると、RTT が過大に評価されて
    pacing と PTO も過大になり、転送が著しく遅くなる。
    """
    elapsed = await _run_transfer(test_certificates, server_to_client=True)
    assert elapsed <= TRANSFER_TIMEOUT_SECONDS, (
        f"32 MiB の転送に {elapsed:.2f} 秒かかった (上限 {TRANSFER_TIMEOUT_SECONDS} 秒)"
    )
