"""WebTransport over HTTP/3 サーバー例

高レベル API を使用した WebTransport over HTTP/3 サーバー実装例。
ストリームとデータグラムの両方をサポート。
"""

import asyncio

from webtransport import h3


async def main() -> None:
    """メイン関数"""
    server = h3.Server(
        host="0.0.0.0",
        port=4433,
        certfile="cert.pem",
        keyfile="key.pem",
    )

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        print(f"WebTransport セッション確立: session_id={session_id} from {addr}")

    async def on_session_closed(session_id: int, addr: tuple[str, int]) -> None:
        print(f"WebTransport セッション終了: session_id={session_id} from {addr}")

    async def on_stream_data(
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        print(f"ストリーム {stream_id} データ受信: {data}")
        # エコーバック
        await server.send_stream_data(addr, stream_id, data)

    async def on_datagram(
        session_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        print(f"データグラム受信: {data}")
        # エコーバック
        await server.send_datagram(addr, session_id, data)

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)
    server.on_stream_data(on_stream_data)
    server.on_datagram(on_datagram)

    async with server:
        print(f"WebTransport over HTTP/3 サーバー開始: {server.host}:{server.actual_port}")
        print("Ctrl+C で終了")
        try:
            await server.run()
        except KeyboardInterrupt:
            pass

    print("サーバー終了")


if __name__ == "__main__":
    print("注意: このサーバーを実行するには証明書が必要です。")
    print("自己署名証明書を生成するには:")
    print("  openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes")
    asyncio.run(main())
