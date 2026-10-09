"""統一 API のセッション終了 (終了コードと理由) の検証

`webtransport.Server` がコールバックへ渡す `Session` ハンドルの
`close_session(error_code, error_message)` と、`webtransport.Client` の
`close(error_code, error_message)` を検証する。同じシナリオを HTTP/2 と
HTTP/3 で回し、プロトコルによらず扱えることを確認する。

ピア側の観測は `on_session_closed` の発火と、ピアの `run()` が記録する終了
原因 (`WebTransportSessionClosedError` の `error_code` / `reason`) で行う。
HTTP/3 はセッションを閉じても QUIC 接続を閉じないため `run()` は受信ループを
継続し、例外は接続終了まで送出されない。ワイヤでの値の観測は
`tests/test_webtransport_h3_client_close.py` と
`tests/test_e2e_webtransport_h3_low_level.py` が担う。
"""

import asyncio

import pytest

from webtransport import Client, HTTPVersion, Server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "http_version",
    [HTTPVersion.HTTP2, HTTPVersion.HTTP3],
    ids=["http2", "http3"],
)
async def test_unified_session_close_session_notifies_peer(
    http_version: HTTPVersion,
    test_certificates,
) -> None:
    """セッションハンドルの close_session でピアが終了コードと理由を観測する

    サーバーが 1 セッションだけを終了コードと理由付きで閉じると、ピアの
    `on_session_closed` が発火し、ピアが記録した終了原因の終了コードと理由が
    指定値と一致することを確認する。ピアの `run()` をタスクで起動してから
    閉じる (run() を起動しないとピアは受信しないため)。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        http_version=http_version,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    server_sessions = []
    session_ready = asyncio.Event()

    async def on_server_session_ready(session) -> None:
        server_sessions.append(session)
        session_ready.set()

    server.on_session_ready(on_server_session_ready)

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

    client_closed = asyncio.Event()

    async def on_client_session_closed(session_id: int) -> None:
        client_closed.set()

    client.on_session_closed(on_client_session_closed)

    async def run_client() -> None:
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(client.connect(), timeout=10.0)
        client_task = asyncio.create_task(run_client())
        await asyncio.wait_for(session_ready.wait(), timeout=10.0)

        # セッションハンドルから 1 セッションだけを閉じる
        await server_sessions[0].close_session(7, "unified close")

        # ピアはセッション終了を検知する
        await asyncio.wait_for(client_closed.wait(), timeout=10.0)

        # ピアが記録した終了原因に指定した終了コードと理由が入る
        terminal_error = client._impl()._terminal_error
        assert terminal_error is not None
        assert terminal_error.error_code == 7
        assert terminal_error.reason == "unified close"
    finally:
        # クライアントを先に閉じる (h2 サーバーの run() はキャンセル時に
        # 接続中ハンドラの完了を待つため、接続を残したまま cancel すると
        # 後片付けが完了しない)
        if client_task is not None:
            client_task.cancel()
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
async def test_unified_client_close_sends_session_close(
    http_version: HTTPVersion,
    test_certificates,
) -> None:
    """Client.close の終了コードと理由付き呼び出しでセッションが終了する

    クライアントの `close(9, "client close")` でサーバー側の
    `on_session_closed` が発火することを確認する。送出値そのものは
    HTTP/3 では低レベル Session の SESSION_CLOSED イベントを持つ
    `test_webtransport_h3_client_close.py` がワイヤで検証する。
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        http_version=http_version,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    session_closed = asyncio.Event()

    async def on_session_closed(session) -> None:
        session_closed.set()

    server.on_session_closed(on_session_closed)

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

    try:
        await asyncio.wait_for(client.connect(), timeout=10.0)

        await client.close(9, "client close")

        await asyncio.wait_for(session_closed.wait(), timeout=10.0)
    finally:
        # クライアントを先に閉じる (h2 サーバーの run() はキャンセル時に
        # 接続中ハンドラの完了を待つため、接続を残したまま cancel すると
        # 後片付けが完了しない)
        await client.close()
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()
