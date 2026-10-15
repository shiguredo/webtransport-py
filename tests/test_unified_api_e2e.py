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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "http_version",
    [HTTPVersion.HTTP2, HTTPVersion.HTTP3],
    ids=["http2", "http3"],
)
async def test_stop_sending_over_unified_api(
    http_version: HTTPVersion,
    test_certificates,
) -> None:
    """ピアの STOP_SENDING が統一 API の on_stop_sending で通知される

    同じシナリオを http_version の切り替えだけで両プロトコルで回す。DUT は
    統一 Server、ピアは統一 Client である。ピアは双方向ストリームを開いて
    データを送り、DUT 側でストリームがセッションへ解決されたことを確認して
    から、統一 API の stop_sending で送信停止を要求する。通知はプロトコルに
    よらず (session, stream_id, error_code) の形で届く。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        http_version=http_version,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    sessions: list[Session] = []
    data_stream_ids: list[int] = []
    stop_sending: list[tuple[Session, int, int | None]] = []
    data_received = asyncio.Event()
    stop_sending_received = asyncio.Event()

    async def on_session_ready(session) -> None:
        sessions.append(session)

    async def on_stream_data(session, stream_id: int, data: bytes) -> None:
        data_stream_ids.append(stream_id)
        data_received.set()

    async def on_stop_sending(session, stream_id: int, error_code: int | None) -> None:
        stop_sending.append((session, stream_id, error_code))
        stop_sending_received.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
    server.on_stop_sending(on_stop_sending)

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

    client_task: asyncio.Task[None] | None = None
    error_code = 0x2A
    try:
        await asyncio.wait_for(client.connect(), timeout=10.0)
        client_task = asyncio.create_task(client.run())

        # ピアが双方向ストリームでデータを送る。DUT がデータを受信した時点で
        # ストリームはセッションへ解決されており、停止要求を受理できる
        stream_id = await client.open_stream()
        assert stream_id >= 0, "WebTransport ストリームを開けませんでした"
        await client.send_stream_data(stream_id, b"stop-me")
        await asyncio.wait_for(data_received.wait(), timeout=10.0)
        assert data_stream_ids == [stream_id]

        # 送信停止の要求は統一 API から送る (プロトコル固有ハンドルへ降りない)
        await client.stop_sending(stream_id, error_code)

        await asyncio.wait_for(stop_sending_received.wait(), timeout=10.0)
        assert len(stop_sending) == 1, "on_stop_sending が 1 回だけ呼ばれません"
        session, notified_stream_id, notified_error_code = stop_sending[0]
        assert notified_stream_id == stream_id
        assert notified_error_code == error_code
        # セッションハンドルはプロトコルと接続に対応する
        assert isinstance(session, Session)
        assert session.http_version is http_version
        assert session.session_id == client.session_id
        assert session.addr is not None
        assert [s.session_id for s in sessions] == [session.session_id]
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
async def test_on_stop_sending_over_unified_api(
    http_version: HTTPVersion,
    test_certificates,
) -> None:
    """サーバーの STOP_SENDING が統一 Client の on_stop_sending で通知される

    同じシナリオを http_version の切り替えだけで両プロトコルで回す。DUT は
    統一 Client、ピアは統一 Server である (上のテストと逆方向)。DUT は
    双方向ストリームを開いてデータを送り、サーバー側でストリームがセッション
    へ解決されたことを確認してから、サーバーがセッションハンドルの
    stop_sending で送信停止を要求する。統一 Client の on_stop_sending には
    (stream_id, error_code) が 1 回だけ届く。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        http_version=http_version,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    server_sessions: list[Session] = []
    server_stream_ids: list[int] = []
    session_ready = asyncio.Event()
    data_received = asyncio.Event()

    async def on_session_ready(session: Session) -> None:
        server_sessions.append(session)
        session_ready.set()

    async def on_stream_data(session: Session, stream_id: int, data: bytes) -> None:
        server_stream_ids.append(stream_id)
        data_received.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

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

    stop_sending: list[tuple[int, int | None]] = []
    stop_sending_received = asyncio.Event()

    async def on_stop_sending(stream_id: int, error_code: int | None) -> None:
        stop_sending.append((stream_id, error_code))
        stop_sending_received.set()

    client.on_stop_sending(on_stop_sending)

    client_task: asyncio.Task[None] | None = None
    error_code = 0x2A
    try:
        await asyncio.wait_for(client.connect(), timeout=10.0)
        client_task = asyncio.create_task(client.run())

        await asyncio.wait_for(session_ready.wait(), timeout=10.0)
        assert client.session_id >= 0, "WebTransport セッションを確立できませんでした"
        session = server_sessions[0]
        assert isinstance(session, Session)

        # DUT が双方向ストリームでデータを送る。サーバーがデータを受信した時点で
        # ストリームはセッションへ解決されており、停止要求を受理できる
        stream_id = await client.open_stream()
        assert stream_id >= 0, "WebTransport ストリームを開けませんでした"
        await client.send_stream_data(stream_id, b"stop-me")
        await asyncio.wait_for(data_received.wait(), timeout=10.0)
        assert server_stream_ids == [stream_id]

        # 送信停止の要求はサーバーのセッションハンドルから送る
        await session.stop_sending(stream_id, error_code)

        await asyncio.wait_for(stop_sending_received.wait(), timeout=10.0)
        # 通知が 1 回だけで、ストリーム ID とエラーコードが往復する
        assert len(stop_sending) == 1, "on_stop_sending が 1 回だけ呼ばれません"
        notified_stream_id, notified_error_code = stop_sending[0]
        assert notified_stream_id == stream_id
        assert notified_error_code == error_code
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
