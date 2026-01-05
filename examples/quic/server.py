"""QUIC サーバー例

高レベル API を使用した QUIC サーバー実装例。
"""

import asyncio

from webtransport import quic


async def main() -> None:
    """メイン関数"""
    server = quic.Server(
        host="0.0.0.0",
        port=4433,
        certfile="cert.pem",
        keyfile="key.pem",
    )

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        print(f"ハンドシェイク完了: {addr}")

    async def on_stream_data(stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]) -> None:
        print(f"ストリーム {stream_id} データ受信: {data}")
        # エコーバック
        await server.send_stream_data(addr, stream_id, data, fin)

    async def on_connection_closed(addr: tuple[str, int]) -> None:
        print(f"接続終了: {addr}")

    server.on_handshake_completed(on_handshake_completed)
    server.on_stream_data(on_stream_data)
    server.on_connection_closed(on_connection_closed)

    async with server:
        print(f"QUIC サーバー開始: {server.host}:{server.actual_port}")
        print("Ctrl+C で終了")
        try:
            await server.run()
        except KeyboardInterrupt:
            pass

    print("サーバー終了")


if __name__ == "__main__":
    asyncio.run(main())
