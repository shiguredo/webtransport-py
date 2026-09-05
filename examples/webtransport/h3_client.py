"""WebTransport over HTTP/3 クライアント例

高レベル API を使用した WebTransport over HTTP/3 クライアント実装例。
ストリームとデータグラムの両方をサポート。
"""

import asyncio

from webtransport import WebTransportConnectError, h3


async def main() -> None:
    """メイン関数"""
    client = h3.Client(
        url="https://localhost:4433/webtransport",
        verify_peer=False,
    )

    async def on_session_ready(session_id: int) -> None:
        print(f"WebTransport セッション確立: session_id={session_id}")

    async def on_session_closed(session_id: int) -> None:
        print(f"WebTransport セッション終了: session_id={session_id}")

    async def on_stream_data(stream_id: int, data: bytes) -> None:
        print(f"ストリーム {stream_id} データ受信: {data}")

    async def on_datagram(data: bytes) -> None:
        print(f"データグラム受信: {data}")

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

    print(f"WebTransport over HTTP/3 クライアント接続: {client.url}")

    # ストリームを開いてデータを送信
    stream_id = await client.open_stream()
    if stream_id >= 0:
        print(f"ストリーム開始: stream_id={stream_id}")
        await client.send_stream_data(stream_id, b"Hello, WebTransport!")

    # データグラムを送信
    await client.send_datagram(b"Datagram test")
    print("データグラム送信完了")

    try:
        await asyncio.wait_for(client.run(), timeout=5.0)
    except TimeoutError:
        pass

    await client.close()
    print("クライアント終了")


if __name__ == "__main__":
    asyncio.run(main())
