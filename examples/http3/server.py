"""HTTP/3 サーバー例

高レベル API を使用した HTTP/3 サーバー実装例。
"""

import asyncio

from webtransport import http3


async def main() -> None:
    """メイン関数"""
    server = http3.Server(
        host="0.0.0.0",
        port=4433,
        certfile="cert.pem",
        keyfile="key.pem",
    )

    async def on_request(
        stream_id: int,
        headers: list[tuple[str, str]],
        addr: tuple[str, int],
    ) -> None:
        print(f"リクエスト受信 (stream_id={stream_id}) from {addr}:")
        for name, value in headers:
            print(f"  {name}: {value}")

        response_headers: list[tuple[str, str]] = [
            (":status", "200"),
            ("content-type", "text/plain"),
        ]
        await server.submit_response(addr, stream_id, response_headers)

        body = b"Hello from HTTP/3 server!"
        await server.send_data(addr, stream_id, body, fin=True)

    server.on_request(on_request)

    async with server:
        print(f"HTTP/3 サーバー開始: {server.host}:{server.actual_port}")
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
