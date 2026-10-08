"""QMux クライアント例

asyncio API を使用した QMux クライアント実装例。
サーバー例 (examples/qmux/server.py) と組み合わせて実行する。

TLS を使う場合は SSLContext と ALPN プロトコルを渡す (draft-ietf-quic-qmux-02
Section 8.1 が QMux over TLS で ALPN を MUST としている)。

    client = qmux.Client(
        "127.0.0.1",
        4433,
        ssl=ssl_context,
        alpn_protocols=["qmux-example"],
    )
"""

import asyncio

from webtransport import qmux

# 接続先 (サーバー例と合わせる)
HOST = "127.0.0.1"
PORT = 4433


async def main() -> None:
    """メイン関数"""
    client = qmux.Client(HOST, PORT)

    received = asyncio.Event()

    async def on_connected() -> None:
        print(f"QMux クライアント接続: {client.peer_address}")

    async def on_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        print(f"ストリーム {stream_id} データ受信: {data} (fin={fin})")
        received.set()

    async def on_closed() -> None:
        print("接続終了")

    client.on_connected(on_connected)
    client.on_stream_data(on_stream_data)
    client.on_closed(on_closed)

    try:
        await client.connect()
    except qmux.exceptions.QmuxError as exc:
        print(f"接続失敗: {exc}")
        return

    stream_id = await client.open_stream()
    print(f"ストリーム開始: {stream_id}")
    await client.send_stream_data(stream_id, b"Hello, QMux!", fin=True)

    run_task = asyncio.create_task(client.run())
    try:
        # サーバーがエコーを返すまで待つ
        await asyncio.wait_for(received.wait(), timeout=5.0)
    except TimeoutError:
        print("受信待ちがタイムアウトしました")
    finally:
        await client.close()
        await run_task


if __name__ == "__main__":
    asyncio.run(main())
