"""QUIC クライアント例

高レベル API を使用した QUIC クライアント実装例。
"""

import asyncio

from webtransport import quic


async def main() -> None:
    """メイン関数"""
    client = quic.Client(
        host="localhost",
        port=4433,
        verify_peer=False,
    )

    async def on_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        print(f"ストリーム {stream_id} データ受信: {data}")

    async def on_connection_closed() -> None:
        print("接続終了")

    client.on_stream_data(on_stream_data)
    client.on_connection_closed(on_connection_closed)

    connected = await client.connect()
    if not connected:
        print("接続失敗")
        return

    print(f"QUIC クライアント接続: {client.host}:{client.port}")

    stream_id = await client.open_stream(bidirectional=True)
    print(f"ストリーム開始: {stream_id}")

    await client.send_stream_data(stream_id, b"Hello, QUIC!")

    try:
        await asyncio.wait_for(client.run(), timeout=5.0)
    except TimeoutError:
        pass

    await client.close()
    print("クライアント終了")


if __name__ == "__main__":
    asyncio.run(main())
