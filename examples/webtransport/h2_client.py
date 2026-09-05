"""WebTransport over HTTP/2 クライアント例

高レベル API を使用した WebTransport over HTTP/2 クライアント実装例。
ストリームと DATAGRAM (Capsule Protocol / RFC 9297) を扱う。
"""

import asyncio

from webtransport import WebTransportConnectError, h2


async def main() -> None:
    """メイン関数"""
    client = h2.Client(
        url="https://localhost:8443/webtransport",
        verify_peer=False,
    )

    async def on_session_ready(session_id: int) -> None:
        print(f"WebTransport セッション確立: session_id={session_id}")

    async def on_session_closed(session_id: int) -> None:
        print(f"WebTransport セッション終了: session_id={session_id}")

    async def on_stream_data(stream_id: int, data: bytes) -> None:
        print(f"ストリーム {stream_id} データ受信: {data}")

    async def on_datagram(data: bytes) -> None:
        print(f"DATAGRAM 受信: {data}")

    client.on_session_ready(on_session_ready)
    client.on_session_closed(on_session_closed)
    client.on_stream_data(on_stream_data)
    client.on_datagram(on_datagram)

    try:
        await client.connect()
    except WebTransportConnectError as exc:
        print(f"接続失敗: {exc}")
        await client.close()
        return

    print(f"WebTransport over HTTP/2 クライアント接続: {client.url}")

    # ストリームを開いてデータを送信
    stream_id = await client.open_stream()
    if stream_id >= 0:
        print(f"ストリーム開始: stream_id={stream_id}")
        await client.send_stream_data(stream_id, b"Hello, WebTransport over HTTP/2!")

    # DATAGRAM を送信 (draft-ietf-webtrans-http2 Capsule)
    await client.send_datagram(b"Hello DATAGRAM")

    try:
        await asyncio.wait_for(client.run(), timeout=5.0)
    except TimeoutError:
        pass

    await client.close()
    print("クライアント終了")


if __name__ == "__main__":
    asyncio.run(main())
