"""統一 API の e2e 検証

同じシナリオを `http_version` の値だけ変えて WebTransport over HTTP/2 と
WebTransport over HTTP/3 の両方で回す。サーバーとクライアントのどちらも統一
API (`webtransport.Server` / `webtransport.Client`) を使い、セッションハンドル
経由の送信が両プロトコルで動くことを確認する。
"""

import asyncio

import pytest

from webtransport import Client, HTTPVersion, Server, Session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "http_version",
    [HTTPVersion.HTTP2, HTTPVersion.HTTP3],
    ids=["http2", "http3"],
)
async def test_datagram_echo_over_unified_api(
    http_version: HTTPVersion,
    test_certificates,
) -> None:
    """同じシナリオが http_version の切り替えだけで両プロトコルで動く

    クライアントがデータグラムを送り、サーバーがセッションハンドルの
    `send_datagram` で返す。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        http_version=http_version,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    server_sessions: list[Session] = []
    session_ready = asyncio.Event()

    async def on_session_ready(session) -> None:
        server_sessions.append(session)
        session_ready.set()

    async def on_datagram(session, data: bytes) -> None:
        # セッションハンドル経由で返す (addr を渡さない)
        await session.send_datagram(data)

    server.on_session_ready(on_session_ready)
    server.on_datagram(on_datagram)

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    await server.start()
    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        http_version=http_version,
        verify_peer=False,
    )

    received: list[bytes] = []
    received_event = asyncio.Event()

    async def on_client_datagram(data: bytes) -> None:
        received.append(data)
        received_event.set()

    client.on_datagram(on_client_datagram)

    client_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(client.connect(), timeout=10.0)
        client_task = asyncio.create_task(client.run())

        await asyncio.wait_for(session_ready.wait(), timeout=10.0)
        assert client.session_id >= 0
        # セッションハンドルがプロトコルと接続元アドレスを持つ
        session = server_sessions[0]
        assert isinstance(session, Session)
        assert session.http_version is http_version
        assert session.session_id == client.session_id
        assert session.addr is not None

        await client.send_datagram(b"unified-api-echo")
        await asyncio.wait_for(received_event.wait(), timeout=10.0)
        assert received == [b"unified-api-echo"]
    finally:
        if client_task is not None:
            client_task.cancel()
        # クライアントを先に閉じる。HTTP/2 の Server.run() はキャンセル時に
        # wait_closed() で接続中ハンドラの終了を待つため、接続を残したまま
        # server_task を cancel すると後片付けが完了しない
        # (_h2_server.Server.stop() は close_clients() で先に接続を閉じる)
        await client.close()
        server_task.cancel()
        await asyncio.gather(
            *[task for task in (client_task, server_task) if task is not None],
            return_exceptions=True,
        )
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "http_version",
    [HTTPVersion.HTTP2, HTTPVersion.HTTP3],
    ids=["http2", "http3"],
)
async def test_session_request_rejection_over_unified_api(
    http_version: HTTPVersion,
    test_certificates,
) -> None:
    """セッション要求の拒否が共通のコールバックで扱える

    `on_session_request` が 403 を返すと、クライアントの `connect()` は
    セッション拒否の例外を送出する。
    """
    from webtransport.h2.exceptions import WebTransportSessionRejectedError as H2Rejected
    from webtransport.h3.exceptions import WebTransportSessionRejectedError as H3Rejected

    server = Server(
        host="127.0.0.1",
        port=0,
        http_version=http_version,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_request(session_id, headers, addr):
        return 403

    server.on_session_request(on_session_request)

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    await server.start()
    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        http_version=http_version,
        verify_peer=False,
    )

    expected = H2Rejected if http_version is HTTPVersion.HTTP2 else H3Rejected
    try:
        with pytest.raises(expected) as exc_info:
            await asyncio.wait_for(client.connect(), timeout=10.0)
        assert exc_info.value.status_code == 403
        assert client.is_connected is False
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()
