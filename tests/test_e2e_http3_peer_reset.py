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


async def _collect_datagrams(server: http3.Server) -> list[bytes]:
    """DUT のソケットに届いたデータグラムを、途切れるまで集める

    固定の待機では全 suite 実行時の遅延で取りこぼすため、読み取りが連続して
    空になるまで (クワイエットになるまで) 集める。
    """
    collected: list[bytes] = []
    idle = 0
    while idle < 5:
        batch = _read_available(server)
        if batch:
            collected.extend(batch)
            idle = 0
        else:
            idle += 1
        await asyncio.sleep(0.02)
    return collected


def _read_available(server: http3.Server) -> list[bytes]:
    """DUT のソケットに溜まっている受信データグラムを読み切る (非ブロッキング)"""
    datagrams: list[bytes] = []
    while True:
        try:
            data, _raw_addr = server._socket.recvfrom(65535)
        except BlockingIOError, InterruptedError:
            break
        datagrams.append(data)
    return datagrams


@pytest.mark.asyncio
@pytest.mark.parametrize("close_connection", [False, True], ids=["reset_only", "reset_and_close"])
async def test_same_batch_headers_before_reset_on_server(
    test_certificates, close_connection: bool
) -> None:
    """サーバー側 DUT: 同一バッチの完備 HEADERS がリセットより先に通知されることを確認

    DUT の run ループを止めてピアにリクエストと RESET_STREAM を別々に
    フラッシュさせ、ソケットに溜まったデータグラムを 1 回の QUIC イベント
    drain (`_drain_quic_events` 1 回) として処理する。転送を保留しない実装では
    QUIC の drain で `on_stream_reset` が先に呼ばれるため、本テストは修正前
    実装で失敗する。
    """
    order: list[str] = []

    async def on_request(
        stream_id: int, headers: list[tuple[str, str]], addr: tuple[str, int]
    ) -> None:
        order.append("request")

    async def on_stream_reset(stream_id: int, error_code: int, addr: tuple[str, int]) -> None:
        order.append("reset")

    server = _create_http3_server(test_certificates)
    server.on_request(on_request)
    server.on_stream_reset(on_stream_reset)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = http3.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    peer_task: asyncio.Task[None] | None = None

    async def run_peer() -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await peer.run()

    try:
        await peer.connect()
        peer_task = asyncio.create_task(run_peer())

        # DUT の run ループを止め、ピアのリクエストと RESET_STREAM を DUT が
        # 読む前にソケットへ溜める
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)

        stream_id = await peer.request("GET", "/same-batch")
        assert stream_id >= 0, "リクエストの送信に失敗しました"
        await peer.reset_stream(stream_id, _RESET_ERROR_CODE)
        if close_connection:
            # リセット直後に接続を閉じ、同一バッチに CONNECTION_CLOSED を並べる。
            # close はパケットを生成するだけなので、収集の前に明示的に送出する
            # (保留したリセットの通知は接続終了の処理後も失われない)
            peer._quic_connection.close(0, "bye")
            await peer._send_pending()

        addr, server_client = next(iter(server._clients.items()))
        datagrams = await _collect_datagrams(server)
        assert datagrams, "ピアのデータグラムが届いていません"

        # 溜まったデータグラムを 1 バッチとして処理する
        assert server_client.quic_connection is not None
        for datagram in datagrams:
            server_client.quic_connection.receive(datagram, server._local_addr, addr)
        await server._drain_quic_events(addr, server_client)
        await server._process_http3_events(addr, server_client)

        assert order == ["request", "reset"], f"コールバックの順序が逆転しています: {order}"
    finally:
        if peer_task is not None:
            peer_task.cancel()
            await asyncio.gather(peer_task, return_exceptions=True)
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await server.stop()
        await peer.close()


@pytest.mark.asyncio
async def test_same_batch_headers_before_reset_on_client(test_certificates) -> None:
    """クライアント側 DUT: 同一バッチの完備 HEADERS がリセットより先に通知されることを確認

    DUT の run ループを止めている間にピアが応答 HEADERS と RESET_STREAM を
    別々にフラッシュし、run ループを再開して `Client._receive` に 1 バッチで
    読ませる。両方が 1 バッチに入らず 2 バッチに分かれても、送信順のとおり
    HEADERS のバッチが先に処理されるため期待する順序になる (バッチをまたぐ
    場合の転送は保留の有無に依存しない)。
    """
    order: list[str] = []
    request_seen = asyncio.Event()
    request_info: dict[str, tuple[str, int]] = {}

    async def on_request(
        stream_id: int, headers: list[tuple[str, str]], addr: tuple[str, int]
    ) -> None:
        request_info["addr"] = addr
        request_seen.set()

    async def on_headers(stream_id: int, headers: list[tuple[str, str]]) -> None:
        order.append("headers")

    async def on_stream_reset(stream_id: int, error_code: int) -> None:
        order.append("reset")

    server = _create_http3_server(test_certificates)
    server.on_request(on_request)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = http3.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    client.on_headers(on_headers)
    client.on_stream_reset(on_stream_reset)
    client_task: asyncio.Task[None] | None = None

    async def run_client() -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await client.run()

    try:
        await client.connect()
        stream_id = await client.request("GET", "/same-batch")
        assert stream_id >= 0, "リクエストの送信に失敗しました"
        client_task = asyncio.create_task(run_client())
        await asyncio.wait_for(request_seen.wait(), timeout=_WAIT_LIMIT)

        # DUT の run ループを止めている間に、ピアの応答 HEADERS と
        # RESET_STREAM を別々のフラッシュで送り切る
        client_task.cancel()
        await asyncio.gather(client_task, return_exceptions=True)
        client_task = None

        addr = request_info["addr"]
        _addr, server_client = next(iter(server._clients.items()))
        await server.submit_response(addr, stream_id, [(":status", "200")])
        assert server_client.quic_connection is not None
        server_client.quic_connection.reset_stream(stream_id, _RESET_ERROR_CODE)
        await server._send_to(addr, server_client)

        # run ループを再開すると _receive が両データグラムを 1 バッチで読む
        client_task = asyncio.create_task(run_client())
        await _wait_until(lambda: len(order) >= 2, f"コールバックが 2 件届きませんでした: {order}")

        assert order == ["headers", "reset"], f"コールバックの順序が逆転しています: {order}"
    finally:
        if client_task is not None:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
        await _stop_http3_server(server, server_task)
        await client.close()


@pytest.mark.asyncio
async def test_reset_only_notifies_once_on_server(test_certificates) -> None:
    """サーバー側 DUT: リセットのみを受信したとき on_stream_reset が 1 回だけ呼ばれることを確認

    ピアは部分的な HEADERS (完備しないため nghttp3 はヘッダーを通知しない) を
    送ってから RESET_STREAM を送る。`on_request` は呼ばれない (対照)。
    """
    order: list[str] = []

    async def on_request(
        stream_id: int, headers: list[tuple[str, str]], addr: tuple[str, int]
    ) -> None:
        order.append("request")

    async def on_stream_reset(stream_id: int, error_code: int, addr: tuple[str, int]) -> None:
        order.append("reset")

    server = _create_http3_server(test_certificates)
    server.on_request(on_request)
    server.on_stream_reset(on_stream_reset)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    peer = quic.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)

    try:
        await peer.connect()
        stream_id = await peer.open_stream(bidirectional=True)
        assert stream_id >= 0, "ストリームを開けませんでした"
        await peer.send_stream_data(stream_id, _PARTIAL_HEADERS, fin=False)
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

        # ピアは RESET_STREAM のみを送る (完備した HEADERS は送らない)
        peer._connection.reset_stream(stream_id, _RESET_ERROR_CODE)
        await peer._send_pending()

        await _wait_until(lambda: bool(order), f"リセットの通知が届きませんでした: {order}")
        await asyncio.sleep(_RETURN_CHECK_SECONDS)
        assert order == ["reset"], f"リセットのみの受信で順序または回数が不正です: {order}"
    finally:
        await _stop_http3_server(server, server_task)
        await peer.close()


@pytest.mark.asyncio
async def test_reset_only_notifies_once_on_client(test_certificates) -> None:
    """クライアント側 DUT: リセットのみを受信したとき on_stream_reset が 1 回だけ呼ばれることを確認

    ピア (http3.Server) が DUT のリクエストを受信してから RESET_STREAM を送る。
    応答 HEADERS は送らないため `on_headers` は呼ばれない (対照)。
    """
    order: list[str] = []
    request_seen = asyncio.Event()
    request_info: dict[str, tuple[str, int]] = {}

    async def on_request(
        stream_id: int, headers: list[tuple[str, str]], addr: tuple[str, int]
    ) -> None:
        request_info["addr"] = addr
        request_seen.set()

    async def on_headers(stream_id: int, headers: list[tuple[str, str]]) -> None:
        order.append("headers")

    async def on_stream_reset(stream_id: int, error_code: int) -> None:
        order.append("reset")

    server = _create_http3_server(test_certificates)
    server.on_request(on_request)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))

    client = http3.Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    client.on_headers(on_headers)
    client.on_stream_reset(on_stream_reset)
    client_task: asyncio.Task[None] | None = None

    async def run_client() -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await client.run()

    try:
        await client.connect()
        stream_id = await client.request("GET", "/reset-only")
        assert stream_id >= 0, "リクエストの送信に失敗しました"
        client_task = asyncio.create_task(run_client())
        await asyncio.wait_for(request_seen.wait(), timeout=_WAIT_LIMIT)

        addr = request_info["addr"]
        _addr, server_client = next(iter(server._clients.items()))
        assert server_client.quic_connection is not None
        server_client.quic_connection.reset_stream(stream_id, _RESET_ERROR_CODE)
        await server._send_to(addr, server_client)

        await _wait_until(lambda: bool(order), f"リセットの通知が届きませんでした: {order}")
        await asyncio.sleep(_RETURN_CHECK_SECONDS)
        assert order == ["reset"], f"リセットのみの受信で順序または回数が不正です: {order}"
    finally:
        if client_task is not None:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
        await _stop_http3_server(server, server_task)
        await client.close()
