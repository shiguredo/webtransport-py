"""HTTP/3 のピア起点 STOP_SENDING 転送テスト

実 QUIC のピアから STOP_SENDING を送り、高レベル層が低レベルの
`Http3Connection` へ書き込み側の終了を伝えることを検証する。転送しないと
nghttp3 は書き込み側を生存とみなし、`nghttp3_conn_writev_stream` がデータを
返し続ける (RFC 9000 Section 3.5 は Ready / Send 状態での RESET_STREAM 送出を
MUST と定めるため、そのデータは破棄される)。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable

import pytest

from webtransport import http3
from webtransport.webtransport_ext import http3 as http3_low

# ピアが送る STOP_SENDING のアプリケーションエラーコード
_STOP_SENDING_ERROR_CODE = 0x0102

# 状態の反映を待つ上限 (秒)
_WAIT_LIMIT = 5.0


async def _wait_until(predicate: Callable[[], bool], message: str) -> None:
    """条件が成立するまで待つ (期限までに成立しなければ失敗する)"""
    deadline = time.monotonic() + _WAIT_LIMIT
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(message)


def _create_http3_server(test_certificates) -> http3.Server:
    """テスト用の高レベル http3.Server を生成する (未起動)"""
    return http3.Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )


async def _run_server(server: http3.Server) -> None:
    """サーバーの run ループをタスクとして実行する"""
    with contextlib.suppress(asyncio.CancelledError):
        await server.run()


async def _stop_http3_server(server: http3.Server, task: asyncio.Task[None]) -> None:
    """サーバーの run ループとサーバーを停止する"""
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await server.stop()


def _send_data_is_noop(http3_connection: http3_low.Connection, stream_id: int) -> bool:
    """書き込み側の終了後は send_data が送信対象を作らないことを確認する

    `get_streams_to_send` は本番の送信経路と同じく nghttp3 から送信すべき
    ストリームを取り出すため、直前に積んだデータが返るかどうかで no-op を
    判別できる (送信バッファの有無だけでは、バッファが事前にある場合に
    判別できない)。送信ループが動いていても、`send_data` の直後に同期的に
    検査するため flush は割り込まない。

    この検査が判別するのは低レベルの契約 (シャットダウン済みなら `send_data` が
    送信対象を作らない) である。nghttp3 側の SHUT_WR だけで実送出が止まる場合
    (サーバー側 DUT ではこちらが先に効く) は本検査では判別できないため、転送の
    有無の主たる表明は `_is_stream_write_shutdown` が担う。
    """
    before = [sid for sid, _data, _fin in http3_connection.get_streams_to_send()]
    assert stream_id not in before, "前提: 検査前から送信対象が残っています"
    http3_connection.send_data(stream_id, b"after-stop-sending", fin=False)
    after = [sid for sid, _data, _fin in http3_connection.get_streams_to_send()]
    return stream_id not in after


@pytest.mark.asyncio
async def test_server_forwards_peer_stop_sending(test_certificates) -> None:
    """サーバー側でピアの STOP_SENDING が nghttp3 へ転送されることを確認

    DUT は高レベル `http3.Server`、ピアは高レベル `http3.Client` である
    (ピアも実装済みの HTTP/3 スタックを使い、QPACK 符号化済みのリクエストで
    DUT の nghttp3 にストリームを作る)。ピアはリクエスト送信後に低レベルの
    `stop_sending` で STOP_SENDING だけを送出する。転送されない実装では
    nghttp3 の書き込み側が生存のままなので、本テストは修正前実装で失敗する。
    """
    request_received = asyncio.Event()
    request_stream_id: dict[str, int] = {}

    async def on_request(
        stream_id: int, headers: list[tuple[str, str]], addr: tuple[str, int]
    ) -> None:
        request_stream_id["stream_id"] = stream_id
        request_received.set()

    server = _create_http3_server(test_certificates)
    server.on_request(on_request)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = http3.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    peer_task: asyncio.Task[None] | None = None

    try:
        await peer.connect()
        stream_id = await peer.request("GET", "/peer-stop-sending")
        assert stream_id >= 0, "リクエストの送信に失敗しました"

        async def run_peer() -> None:
            with contextlib.suppress(asyncio.CancelledError):
                await peer.run()

        peer_task = asyncio.create_task(run_peer())
        await asyncio.wait_for(request_received.wait(), timeout=_WAIT_LIMIT)
        assert request_stream_id["stream_id"] == stream_id

        # DUT 側の低レベル接続を取得する
        await _wait_until(
            lambda: bool(server._clients),
            "サーバーがクライアントを登録しませんでした",
        )
        _addr, server_client = next(iter(server._clients.items()))
        http3_connection = server_client.http3_connection
        assert http3_connection is not None

        # ピアは STOP_SENDING だけを送る (低レベル API はキューに積むだけなので
        # 明示的にフラッシュする)
        peer._quic_connection.stop_sending(stream_id, _STOP_SENDING_ERROR_CODE)
        await peer._send_pending()

        # 転送されると nghttp3 書き込み側の終了が記録され、以後の send_data が
        # 送信対象を作らなくなる
        await _wait_until(
            lambda: http3_connection._is_stream_write_shutdown(stream_id) is True,
            "ピアの STOP_SENDING 後も書き込み側の終了が nghttp3 へ伝わっていません",
        )
        assert _send_data_is_noop(http3_connection, stream_id), (
            "STOP_SENDING の転送後も send_data が送信対象を作っています"
        )
    finally:
        if peer_task is not None:
            peer_task.cancel()
            await asyncio.gather(peer_task, return_exceptions=True)
        await _stop_http3_server(server, server_task)
        await peer.close()


@pytest.mark.asyncio
async def test_client_forwards_peer_stop_sending(test_certificates) -> None:
    """クライアント側でもピアの STOP_SENDING が nghttp3 へ転送されることを確認

    高レベル `http3.Client` の STOP_SENDING 分岐が低レベル `Http3Connection` へ
    書き込み側の終了を伝える (サーバー側と対称)。ピアの `http3.Server` は
    リクエスト受信後に低レベルの `stop_sending` で STOP_SENDING だけを送出する。
    `Client.request` は FIN を送らないため、ピアから見て自側の書き込み側は
    終端しておらず STOP_SENDING が送出される。
    """
    request_received = asyncio.Event()

    async def on_request(
        stream_id: int, headers: list[tuple[str, str]], addr: tuple[str, int]
    ) -> None:
        request_received.set()

    server = _create_http3_server(test_certificates)
    server.on_request(on_request)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = http3.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    client_task: asyncio.Task[None] | None = None

    try:
        await client.connect()
        stream_id = await client.request("GET", "/peer-stop-sending")
        assert stream_id >= 0, "リクエストの送信に失敗しました"

        async def run_client() -> None:
            with contextlib.suppress(asyncio.CancelledError):
                await client.run()

        client_task = asyncio.create_task(run_client())
        await asyncio.wait_for(request_received.wait(), timeout=_WAIT_LIMIT)

        http3_connection = client._http3_connection
        assert http3_connection is not None

        # ピアは STOP_SENDING だけを送る (RESET_STREAM は送らない)
        await _wait_until(
            lambda: bool(server._clients),
            "サーバーがクライアントを登録しませんでした",
        )
        addr, server_client = next(iter(server._clients.items()))
        peer_connection = server_client.quic_connection
        assert peer_connection is not None
        peer_connection.stop_sending(stream_id, _STOP_SENDING_ERROR_CODE)
        await server._send_to(addr, server_client)

        # 転送されると nghttp3 書き込み側の終了が記録され、以後の send_data が
        # 送信対象を作らなくなる
        await _wait_until(
            lambda: http3_connection._is_stream_write_shutdown(stream_id) is True,
            "ピアの STOP_SENDING 後も書き込み側の終了が nghttp3 へ伝わっていません",
        )
        assert _send_data_is_noop(http3_connection, stream_id), (
            "STOP_SENDING の転送後も send_data が送信対象を作っています"
        )
    finally:
        if client_task is not None:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
        await _stop_http3_server(server, server_task)
        await client.close()
