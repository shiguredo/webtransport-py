"""WebTransport over HTTP/2 サーバー例

高レベル API を使用した WebTransport over HTTP/2 サーバー実装例。
ストリームと DATAGRAM (Capsule Protocol / RFC 9297) を扱う。
"""

import asyncio

from webtransport import HTTPVersion, Server, Session


async def main() -> None:
    """メイン関数"""
    server = Server(
        host="0.0.0.0",
        port=8443,
        http_version=HTTPVersion.HTTP2,
        certfile="cert.pem",
        keyfile="key.pem",
    )

    async def on_session_ready(session: Session) -> None:
        print(f"WebTransport セッション確立: session_id={session.session_id}")

    async def on_session_closed(session: Session) -> None:
        print(f"WebTransport セッション終了: session_id={session.session_id}")

    async def on_stream_data(session: Session, stream_id: int, data: bytes) -> None:
        print(f"ストリーム {stream_id} データ受信: {data}")
        # エコーバック (セッションハンドル経由で送る)
        await session.send_stream_data(stream_id, data)

    async def on_datagram(session: Session, data: bytes) -> None:
        print(f"DATAGRAM 受信: {data}")
        await session.send_datagram(data)

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)
    server.on_stream_data(on_stream_data)
    server.on_datagram(on_datagram)

    async with server:
        print(f"WebTransport over HTTP/2 サーバー開始: {server.host}:{server.actual_port}")
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
