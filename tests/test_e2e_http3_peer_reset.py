"""HTTP/3 のピア起点 RESET_STREAM 転送テスト

実 QUIC のピアで「部分的な HEADERS + RESET_STREAM」を再現し、高レベル層が
低レベルの `Http3Connection` へ読み取り中断を伝えることを検証する。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable

import pytest
from conftest import _encode_varint

from webtransport import http3, quic

# HEADERS フレーム (Type 0x01) の Length を実際に渡すペイロードより大きく宣言し、
# ヘッダーブロックの受信途中の状態を作る (end_headers_cb が発火しない)
_PARTIAL_HEADERS = bytes([0x01]) + _encode_varint(100) + b"\x00\x00"

# ピアが送るリセットのアプリケーションエラーコード
_RESET_ERROR_CODE = 0x0102

# 状態の反映を待つ上限 (秒)
_WAIT_LIMIT = 5.0

# 返送の有無を確認する猶予 (秒)。返送していれば 1 往復で届くため短くてよい
_RETURN_CHECK_SECONDS = 0.3


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


@pytest.mark.asyncio
async def test_peer_reset_stream_stops_reading(test_certificates) -> None:
    """ピアの RESET_STREAM で受信途中のヘッダーブロックが解放されることを確認

    実 UDP の `quic.Client` をピアとして高レベル `http3.Server` に接続し、
    部分的な HEADERS フレームを送ったあとで低レベル `reset_stream` により
    RESET_STREAM のみを送る (`quic.Client.shutdown_stream` は STOP_SENDING も
    送るため、RFC 9000 Section 3.5 の MUST により DUT が必ず RESET_STREAM を
    返し、「返送しない」ことを検証できない)。転送されない実装ではエントリが
    残るため、本テストは修正前実装で失敗する。
    """
    reset_info: dict[str, int] = {}
    reset_received = asyncio.Event()

    async def on_stream_reset(stream_id: int, error_code: int, addr: tuple[str, int]) -> None:
        reset_info["stream_id"] = stream_id
        reset_info["error_code"] = error_code
        reset_received.set()

    server = _create_http3_server(test_certificates)
    server.on_stream_reset(on_stream_reset)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = quic.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)

    try:
        await peer.connect()
        stream_id = await peer.open_stream(bidirectional=True)
        assert stream_id >= 0, "ストリームを開けませんでした"
        await peer.send_stream_data(stream_id, _PARTIAL_HEADERS, fin=False)

        # DUT 側で受信途中のヘッダーブロックのエントリが作られるまで待つ
        await _wait_until(
            lambda: bool(server._clients),
            "サーバーがクライアントを登録しませんでした",
        )
        _addr, server_client = next(iter(server._clients.items()))
        http3_connection = server_client.http3_connection
        assert http3_connection is not None
        await _wait_until(
            lambda: http3_connection._has_pending_headers(stream_id) is True,
            "受信途中のヘッダーブロックのエントリが作られていません",
        )

        # ピアは RESET_STREAM のみを送る (低レベル API はキューに積むだけなので
        # 明示的にフラッシュする)
        peer._connection.reset_stream(stream_id, _RESET_ERROR_CODE)
        await peer._send_pending()

        await asyncio.wait_for(reset_received.wait(), timeout=_WAIT_LIMIT)
        assert reset_info["stream_id"] == stream_id
        assert reset_info["error_code"] == _RESET_ERROR_CODE

        # 転送されていればエントリが解放される (されない場合は接続終了まで残る)
        await _wait_until(
            lambda: http3_connection._has_pending_headers(stream_id) is None,
            "ピアのリセット後も受信途中のヘッダーブロックのエントリが残っています",
        )

        # RESET_STREAM を返送しない。wait_for_stream_reset は接続終了時にも
        # TimeoutError を送出するため、直前までの表明で接続が生きていること
        # (リセットが届き、コールバックが発火したこと) を前提にする。
        # 返送していれば即座に受信して返り、TimeoutError にならない
        with pytest.raises(TimeoutError):
            await peer.wait_for_stream_reset(stream_id, 0.5)
    finally:
        await _stop_http3_server(server, server_task)
        await peer.close()


@pytest.mark.asyncio
async def test_client_forwards_peer_reset_stream(test_certificates) -> None:
    """クライアント側でもピアの RESET_STREAM が nghttp3 へ転送されることを確認

    高レベル Client の STREAM_RESET 分岐が低レベル Http3Connection へ読み取り
    中断を伝える (サーバー側と対称)。ピアの http3.Server は生の QUIC ストリーム
    データで部分的な応答 HEADERS を送出してから RESET_STREAM のみを送る。
    """
    request_received = asyncio.Event()
    reset_info: dict[str, int] = {}
    reset_received = asyncio.Event()
    peer_reset_seen = False

    async def on_request(
        stream_id: int, headers: list[tuple[str, str]], addr: tuple[str, int]
    ) -> None:
        request_received.set()

    async def on_peer_stream_reset(stream_id: int, error_code: int, addr: tuple[str, int]) -> None:
        nonlocal peer_reset_seen
        peer_reset_seen = True

    server = _create_http3_server(test_certificates)
    server.on_request(on_request)
    server.on_stream_reset(on_peer_stream_reset)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = http3.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)

    async def on_stream_reset(stream_id: int, error_code: int) -> None:
        reset_info["stream_id"] = stream_id
        reset_info["error_code"] = error_code
        reset_received.set()

    client.on_stream_reset(on_stream_reset)
    client_task: asyncio.Task[None] | None = None

    try:
        await client.connect()
        stream_id = await client.request("GET", "/peer-reset")
        assert stream_id >= 0, "リクエストの送信に失敗しました"

        async def run_client() -> None:
            with contextlib.suppress(asyncio.CancelledError):
                await client.run()

        client_task = asyncio.create_task(run_client())
        await asyncio.wait_for(request_received.wait(), timeout=_WAIT_LIMIT)

        # ピアが部分的な応答 HEADERS を生バイトで送出する (nghttp3 を通さない)
        await _wait_until(
            lambda: bool(server._clients),
            "サーバーがクライアントを登録しませんでした",
        )
        addr, server_client = next(iter(server._clients.items()))
        peer_connection = server_client.quic_connection
        assert peer_connection is not None
        peer_connection.send_stream_data(stream_id, _PARTIAL_HEADERS, False)
        await server._send_to(addr, server_client)

        http3_connection = client._http3_connection
        assert http3_connection is not None
        await _wait_until(
            lambda: http3_connection._has_pending_headers(stream_id) is True,
            "クライアント側で受信途中のヘッダーブロックのエントリが作られていません",
        )

        # ピアは RESET_STREAM のみを送る (STOP_SENDING は送らない)
        peer_connection.reset_stream(stream_id, _RESET_ERROR_CODE)
        await server._send_to(addr, server_client)

        await asyncio.wait_for(reset_received.wait(), timeout=_WAIT_LIMIT)
        assert reset_info["stream_id"] == stream_id
        assert reset_info["error_code"] == _RESET_ERROR_CODE

        # 転送されていればエントリが解放される (されない場合は残る)
        await _wait_until(
            lambda: http3_connection._has_pending_headers(stream_id) is None,
            "ピアのリセット後もクライアント側で受信途中のヘッダーブロックが残っています",
        )

        # RESET_STREAM を返送しない。返送していればピア (http3.Server) の
        # on_stream_reset が発火する (reset_stream を流用した実装の検出)
        await asyncio.sleep(_RETURN_CHECK_SECONDS)
        assert peer_reset_seen is False, "クライアントがピアへ RESET_STREAM を返送しています"
    finally:
        if client_task is not None:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
        await _stop_http3_server(server, server_task)
        await client.close()
