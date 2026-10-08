"""QMux サーバー例

asyncio API を使用した QMux サーバー実装例。
受信した双方向ストリームのデータをエコーバックする。

TLS を使う場合は SSLContext と ALPN プロトコルを渡す (draft-ietf-quic-qmux-02
Section 8.1 が QMux over TLS で ALPN を MUST としている)。

    server = qmux.Server(
        "127.0.0.1",
        4433,
        ssl=ssl_context,
        alpn_protocols=["qmux-example"],
    )
"""

import asyncio

from webtransport import qmux

# 待ち受けアドレス (クライアント例と合わせる)
HOST = "127.0.0.1"
PORT = 4433


async def main() -> None:
    """メイン関数"""
    server = qmux.Server(HOST, PORT)

    async def on_session_ready(session: qmux.Session) -> None:
        print(f"セッション開始: session_id={session.session_id} peer={session.peer_address}")

    async def on_session_closed(session: qmux.Session) -> None:
        print(f"セッション終了: session_id={session.session_id}")

    async def on_stream_data(
        session: qmux.Session,
        stream_id: int,
        data: bytes,
        fin: bool,
    ) -> None:
        print(f"ストリーム {stream_id} データ受信: {data} (fin={fin})")
        # 双方向ストリーム (stream_id % 4 == 0) のみエコーする
        if stream_id % 4 == 0:
            await session.send_stream_data(stream_id, data, fin=fin)

    server.on_session_ready(on_session_ready)
    server.on_session_closed(on_session_closed)
    server.on_stream_data(on_stream_data)

    await server.start()
    print(f"QMux サーバー起動: {HOST}:{server.actual_port}")
    try:
        await server.run()
    except asyncio.CancelledError:
        pass
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
