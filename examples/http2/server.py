"""HTTP/2 サーバー例

高レベル API を使用した HTTP/2 サーバー実装例。
"""

import asyncio

from webtransport import http2


async def main() -> None:
    """メイン関数"""
    server = http2.Server(
        host="0.0.0.0",
        port=8443,
        certfile="cert.pem",
        keyfile="key.pem",
    )

    # リクエストボディを stream_id ごとに溜める
    bodies: dict[int, bytearray] = {}

    async def on_request(
        stream_id: int,
        headers: list[tuple[str, str]],
        response_writer: http2.ResponseWriter,
    ) -> None:
        print(f"リクエスト受信 (stream_id={stream_id}):")
        for name, value in headers:
            print(f"  {name}: {value}")
        bodies[stream_id] = bytearray()

    async def on_data(
        stream_id: int,
        data: bytes,
        response_writer: http2.ResponseWriter,
    ) -> None:
        bodies.setdefault(stream_id, bytearray()).extend(data)

    async def on_stream_end(
        stream_id: int,
        response_writer: http2.ResponseWriter,
    ) -> None:
        """ボディ受信の完了を検知してから応答する"""
        body = bytes(bodies.pop(stream_id, b""))
        print(f"ボディ受信完了 (stream_id={stream_id}): {len(body)} バイト")

        response_headers: list[tuple[str, str]] = [
            (":status", "200"),
            ("content-type", "text/plain"),
        ]
        await response_writer.send_headers(stream_id, response_headers)

        response_body = b"Hello from HTTP/2 server! received: " + str(len(body)).encode()
        await response_writer.send_data(stream_id, response_body, end_stream=True)

    server.on_request(on_request)
    server.on_data(on_data)
    server.on_stream_end(on_stream_end)

    async with server:
        print(f"HTTP/2 サーバー開始: {server.host}:{server.actual_port}")
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
