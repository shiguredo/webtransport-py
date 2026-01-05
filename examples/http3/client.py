"""HTTP/3 クライアント例

高レベル API を使用した HTTP/3 クライアント実装例。
"""

import asyncio

from webtransport import http3


async def main() -> None:
    """メイン関数"""
    client = http3.Client(
        host="www.google.com",
        port=443,
    )

    async def on_headers(stream_id: int, headers: list[tuple[str, str]]) -> None:
        print(f"ストリーム {stream_id} ヘッダー受信:")
        for name, value in headers:
            print(f"  {name}: {value}")

    async def on_data(stream_id: int, data: bytes) -> None:
        print(f"ストリーム {stream_id} データ受信: {len(data)} バイト")
        print(f"  {data[:100]}")

    async def on_stream_end(stream_id: int) -> None:
        print(f"ストリーム {stream_id} 終了")

    client.on_headers(on_headers)
    client.on_data(on_data)
    client.on_stream_end(on_stream_end)

    connected = await client.connect()
    if not connected:
        print("接続失敗")
        return

    print(f"HTTP/3 クライアント接続: {client.host}:{client.port}")

    stream_id = await client.request("GET", "/")
    print(f"リクエスト送信: GET / (stream_id={stream_id})")

    try:
        await asyncio.wait_for(client.run(), timeout=5.0)
    except TimeoutError:
        pass

    await client.close()
    print("クライアント終了")


if __name__ == "__main__":
    asyncio.run(main())
