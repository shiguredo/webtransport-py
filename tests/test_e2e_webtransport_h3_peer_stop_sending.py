"""WebTransport over HTTP/3 のピア起点 STOP_SENDING テスト

実 QUIC のピアから STOP_SENDING を送り、高レベル層 (h3 クライアント / サーバー) が
nghttp3 へ書き込み側の終了を伝え、アプリへ通知することを検証する。転送しないと
nghttp3 は書き込み側を生存とみなし、送信データを返し続ける (RFC 9000 Section 3.5 は
Ready / Send 状態での RESET_STREAM 送出を MUST と定めるため、そのデータは破棄される)。

対照として、WebTransport 以外のストリームと CONNECT ストリーム (セッション制御
ストリーム) の STOP_SENDING は通知も転送も対象外であることも確認する。
"""

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from conftest import _encode_wt_stream_data

from webtransport._h3_client import Client
from webtransport._h3_server import Server
from webtransport.h3._error_codes import webtransport_code_to_http_code
from webtransport.h3.exceptions import WebTransportSessionClosedError

# ピアが送る STOP_SENDING のアプリケーションエラーコード。WT_APPLICATION_ERROR
# レンジのワイヤコードへリマップして送り、通知時にアプリコードへ逆変換される
# ことを確認する (draft-ietf-webtrans-http3-16 Section 4.4)
_APP_ERROR_CODE = 7

# WT_APPLICATION_ERROR レンジ外の HTTP/3 エラーコード。アプリコードへ逆変換
# できないため通知時は None になる (on_stream_reset と同じ規則)
_HTTP3_ERROR_CODE = 0x0102

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


async def _run_server(server: Server) -> None:
    """サーバーの run ループをタスクとして実行する"""
    with contextlib.suppress(asyncio.CancelledError):
        await server.run()


async def _run_client(client: Client) -> None:
    """クライアントの run ループをタスクとして実行する"""
    with contextlib.suppress(asyncio.CancelledError):
        await client.run()


async def _run_client_swallowing_session_close(client: Client) -> None:
    """クライアントの run ループをタスクとして実行する (セッション終了を握る)

    CONNECT ストリームのリセットでセッションが終了した場合は run() が
    WebTransportSessionClosedError を送出して終了する。対照の検証ではピアの
    セッション終了が意図した結果であるため、後片付けに進めるようここで握る
    """
    with contextlib.suppress(asyncio.CancelledError, WebTransportSessionClosedError):
        await client.run()


async def _stop_server(server: Server, task: asyncio.Task[None]) -> None:
    """サーバーの run ループとサーバーを停止する"""
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await server.stop()


@dataclass
class _ServerObservation:
    """サーバー側コールバックの観測結果"""

    session_ids: list[int] = field(default_factory=list)
    addrs: list[tuple[str, int]] = field(default_factory=list)
    data_stream_ids: list[int] = field(default_factory=list)
    stop_sending: list[tuple[int, int, int | None, tuple[str, int]]] = field(default_factory=list)
    data_received: asyncio.Event = field(default_factory=asyncio.Event)


async def _start_observation_server(
    test_certificates,
) -> tuple[Server, asyncio.Task[None], _ServerObservation]:
    """セッション確立・データ受信・STOP_SENDING 通知を観測するサーバーを起動する

    高レベル h3 Server を DUT またはピアとして使う。on_stop_sending は
    (session_id, stream_id, error_code, addr) を到着順に記録する

    Returns:
        (server, server_task, info) のタプル
    """
    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    info = _ServerObservation()

    async def on_session_ready(session_id: int, addr: tuple[str, int]) -> None:
        info.session_ids.append(session_id)
        info.addrs.append(addr)

    async def on_stream_data(
        session_id: int,
        stream_id: int,
        data: bytes,
        addr: tuple[str, int],
    ) -> None:
        info.data_stream_ids.append(stream_id)
        info.data_received.set()

    async def on_stop_sending(
        session_id: int,
        stream_id: int,
        error_code: int | None,
        addr: tuple[str, int],
    ) -> None:
        info.stop_sending.append((session_id, stream_id, error_code, addr))

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)
    server.on_stop_sending(on_stop_sending)

    # サーバーの起動を完了させてからタスクを作成する (run() は未開始状態だと
    # RuntimeError を上げるため)
    await server.start()
    server_task = asyncio.create_task(_run_server(server))
    return server, server_task, info


@dataclass
class _ClientObservation:
    """クライアント側コールバックの観測結果"""

    stop_sending: list[tuple[int, int | None]] = field(default_factory=list)


@pytest.mark.asyncio
async def test_server_notifies_peer_stop_sending(test_certificates) -> None:
    """サーバー側でピアの STOP_SENDING が nghttp3 へ転送されアプリへ通知されることを確認

    DUT は高レベル h3 Server、ピアは高レベル h3 Client である。ピアは
    WebTransport データストリームを開いてデータを送り、DUT 側でストリームが
    解決されたことを確認してから、低レベルの stop_sending で STOP_SENDING だけを
    送出する (RESET_STREAM は送らない)。error_code は on_stream_reset と同じ規則で
    アプリコードへ逆変換される。
    """
    server, server_task, info = await _start_observation_server(test_certificates)
    peer = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    peer_task: asyncio.Task[None] | None = None

    try:
        await peer.connect()
        peer_task = asyncio.create_task(_run_client(peer))

        stream_id = await peer.open_stream()
        assert stream_id >= 0, "WebTransport ストリームを開けませんでした"
        await peer.send_stream_data(stream_id, b"hello")
        await _wait_until(
            lambda: info.data_received.is_set(),
            "サーバーがデータを受信しませんでした",
        )

        # ピアは STOP_SENDING だけを送る (低レベル API はキューに積むだけなので
        # 明示的にフラッシュする)
        quic_connection = peer._quic_connection
        assert quic_connection is not None
        quic_connection.stop_sending(
            stream_id,
            webtransport_code_to_http_code(_APP_ERROR_CODE),
        )
        await peer._send_pending()

        await _wait_until(
            lambda: bool(info.stop_sending),
            "ピアの STOP_SENDING 後も on_stop_sending が呼ばれません",
        )
        session_id, notified_stream_id, error_code, addr = info.stop_sending[0]
        assert notified_stream_id == stream_id
        assert session_id == info.session_ids[0]
        assert addr == info.addrs[0]
        assert error_code == _APP_ERROR_CODE, (
            "error_code が WT_APPLICATION_ERROR レンジのアプリコードへ逆変換されていません"
        )

        # 転送されると nghttp3 の書き込み側が終了し、以後そのストリームは
        # 書き込み不可になる
        server_client = server._clients[addr]
        assert server_client.webtransport_session.stream_writable(stream_id) == 0, (
            "ピアの STOP_SENDING 後も書き込み側の終了が nghttp3 へ伝わっていません"
        )
        # シャットダウン済みストリームへの send_stream_data は no-op となり
        # 送信バッファも作らない
        await server.send_stream_data(addr, stream_id, b"after-stop-sending")
        assert server_client.webtransport_session._has_stream_buffer(stream_id) is None, (
            "シャットダウン済みストリームへの send_stream_data がバッファを積んでいます"
        )
    finally:
        if peer_task is not None:
            peer_task.cancel()
            await asyncio.gather(peer_task, return_exceptions=True)
        await _stop_server(server, server_task)
        await peer.close()


@pytest.mark.asyncio
async def test_client_notifies_peer_stop_sending(test_certificates) -> None:
    """クライアント側でピアの STOP_SENDING が nghttp3 へ転送されアプリへ通知されることを確認

    DUT は高レベル h3 Client、ピアは高レベル h3 Server である (サーバー側と
    対称)。クライアントは WebTransport データストリームを 2 本開き、ピアは
    1 本目へ WT_APPLICATION_ERROR レンジのコードを、2 本目へレンジ外の HTTP/3
    エラーコードを STOP_SENDING で送る。通知される error_code は on_stream_reset
    と同じ規則で変換され、レンジ外は None になる。
    """
    server, server_task, info = await _start_observation_server(test_certificates)
    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    observation = _ClientObservation()

    async def on_stop_sending(stream_id: int, error_code: int | None) -> None:
        observation.stop_sending.append((stream_id, error_code))

    client.on_stop_sending(on_stop_sending)
    client_task: asyncio.Task[None] | None = None

    try:
        await client.connect()
        client_task = asyncio.create_task(_run_client(client))

        stream_id = await client.open_stream()
        assert stream_id >= 0, "WebTransport ストリームを開けませんでした"
        await client.send_stream_data(stream_id, b"hello")
        await _wait_until(
            lambda: stream_id in info.data_stream_ids,
            "サーバーがデータを受信しませんでした",
        )

        # ピアは STOP_SENDING だけを送る (低レベル API はキューに積むだけなので
        # 明示的にフラッシュする)
        await _wait_until(
            lambda: bool(server._clients),
            "サーバーがクライアントを登録しませんでした",
        )
        addr, server_client = next(iter(server._clients.items()))
        quic_connection = server_client.quic_connection
        assert quic_connection is not None
        quic_connection.stop_sending(
            stream_id,
            webtransport_code_to_http_code(_APP_ERROR_CODE),
        )
        await server._send_to(addr, server_client)

        await _wait_until(
            lambda: bool(observation.stop_sending),
            "ピアの STOP_SENDING 後も on_stop_sending が呼ばれません",
        )
        assert observation.stop_sending[0] == (stream_id, _APP_ERROR_CODE), (
            "error_code が WT_APPLICATION_ERROR レンジのアプリコードへ逆変換されていません"
        )

        # 転送されると nghttp3 の書き込み側が終了し、以後そのストリームは
        # 書き込み不可になる
        webtransport_session = client._webtransport_session
        assert webtransport_session is not None
        assert webtransport_session.stream_writable(stream_id) == 0, (
            "ピアの STOP_SENDING 後も書き込み側の終了が nghttp3 へ伝わっていません"
        )
        # シャットダウン済みストリームへの send_stream_data は no-op となり
        # 送信バッファも作らない
        await client.send_stream_data(stream_id, b"after-stop-sending")
        assert webtransport_session._has_stream_buffer(stream_id) is None, (
            "シャットダウン済みストリームへの send_stream_data がバッファを積んでいます"
        )

        # WT_APPLICATION_ERROR レンジ外のコードはアプリコードへ逆変換できないため
        # None で通知される
        second_stream_id = await client.open_stream()
        assert second_stream_id >= 0, "WebTransport ストリームを開けませんでした"
        await client.send_stream_data(second_stream_id, b"hello")
        await _wait_until(
            lambda: second_stream_id in info.data_stream_ids,
            "サーバーが 2 本目のデータを受信しませんでした",
        )
        quic_connection.stop_sending(second_stream_id, _HTTP3_ERROR_CODE)
        await server._send_to(addr, server_client)
        await _wait_until(
            lambda: len(observation.stop_sending) == 2,
            "2 本目の STOP_SENDING で on_stop_sending が呼ばれません",
        )
        assert observation.stop_sending[1] == (second_stream_id, None), (
            "レンジ外の error_code が None になりません"
        )
    finally:
        if client_task is not None:
            client_task.cancel()
            await asyncio.gather(client_task, return_exceptions=True)
        await _stop_server(server, server_task)
        await client.close()


@pytest.mark.asyncio
async def test_server_ignores_stop_sending_for_non_webtransport_streams(
    test_certificates,
) -> None:
    """WebTransport 以外のストリームと CONNECT ストリームの STOP_SENDING を通知しないことを確認

    DUT は高レベル h3 Server、ピアは高レベル h3 Client である。対照は 2 つある。
    1 つ目は DUT が開いた WT 管理外の双方向ストリーム (DUT の nghttp3 に登録しない
    ため DUT はセッション ID を解決できない)、2 つ目はピアの CONNECT ストリーム
    (セッション制御ストリーム) である。

    通知の有無は競合しないよう、STOP_SENDING を送った直後にピアが別の WT データ
    ストリームへデータを送り、DUT 側の on_stream_data が発火した時点で判定する。
    DUT は QUIC イベントを到着順に処理するため、先に届いた STOP_SENDING の処理は
    on_stream_data の発火より前に完了している。

    CONNECT ストリームへ送った場合は、nghttp3 の書き込み側も生存のままである
    (転送しない) ことも確認する。
    """
    server, server_task, info = await _start_observation_server(test_certificates)
    # セッションは CONNECT ストリームのリセットで終了するため、close() の
    # ピア終了待ちは行わない
    peer = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
        close_wait_timeout=0,
    )
    peer_task: asyncio.Task[None] | None = None

    try:
        await peer.connect()
        peer_task = asyncio.create_task(_run_client_swallowing_session_close(peer))
        await _wait_until(
            lambda: bool(server._clients),
            "サーバーがクライアントを登録しませんでした",
        )
        addr, server_client = next(iter(server._clients.items()))
        quic_connection = server_client.quic_connection
        assert quic_connection is not None
        peer_quic_connection = peer._quic_connection
        assert peer_quic_connection is not None

        # 対照 1: WT 管理外の双方向ストリームを DUT が開く。WT データストリームの
        # ヘッダーだけをワイヤで送るため DUT の nghttp3 には登録されず、DUT は
        # セッション ID を解決できない。ピア側の nghttp3 には WT ストリームとして
        # 登録されるため、ピアはストリームを持ち STOP_SENDING を送れる (ピアの
        # ngtcp2 はストリームを持たないストリームへの shutdown_stream_read を
        # 黙って無視するため、ヘッダーを送らないと検証が空振りする)
        plain_stream_id = quic_connection.open_stream(True)
        assert plain_stream_id >= 0, "双方向ストリームを開けませんでした"
        connect_stream_id = peer.session_id
        assert connect_stream_id >= 0, "セッションが確立していません"
        quic_connection.send_stream_data(
            plain_stream_id,
            _encode_wt_stream_data(connect_stream_id, b""),
            fin=False,
        )
        await server._send_to(addr, server_client)
        peer_session = peer._webtransport_session
        assert peer_session is not None
        await _wait_until(
            lambda: peer_session.stream_wt_session_id(plain_stream_id) == connect_stream_id,
            "ピアが WT ヘッダーを受信しませんでした",
        )

        sync_stream_id = await peer.open_stream()
        assert sync_stream_id >= 0, "WebTransport ストリームを開けませんでした"
        peer_quic_connection.stop_sending(
            plain_stream_id,
            webtransport_code_to_http_code(_APP_ERROR_CODE),
        )
        await peer.send_stream_data(sync_stream_id, b"sync-1")
        await _wait_until(
            lambda: sync_stream_id in info.data_stream_ids,
            "同期用のデータが届きませんでした",
        )
        assert info.stop_sending == [], (
            "WebTransport 以外のストリームの STOP_SENDING を通知しています"
        )

        # 対照 2: ピアの CONNECT ストリーム (セッション制御ストリーム)
        # 転送の有無を判定するため、送信前の書き込み可否を記録する
        writable_before = server_client.webtransport_session.stream_writable(connect_stream_id)
        assert writable_before == 1, "前提: CONNECT ストリームは書き込み可能です"
        second_sync_stream_id = await peer.open_stream()
        assert second_sync_stream_id >= 0, "WebTransport ストリームを開けませんでした"
        peer_quic_connection.stop_sending(connect_stream_id, _HTTP3_ERROR_CODE)
        await peer.send_stream_data(second_sync_stream_id, b"sync-2")
        await _wait_until(
            lambda: second_sync_stream_id in info.data_stream_ids,
            "同期用のデータが届きませんでした",
        )
        assert info.stop_sending == [], "CONNECT ストリームの STOP_SENDING を通知しています"
        # nghttp3 へは転送しない (書き込み側の状態が変わらない)
        assert (
            server_client.webtransport_session.stream_writable(connect_stream_id) == writable_before
        ), "CONNECT ストリームの書き込み側の終了が nghttp3 へ伝わっています"
    finally:
        if peer_task is not None:
            peer_task.cancel()
            await asyncio.gather(peer_task, return_exceptions=True)
        await _stop_server(server, server_task)
        await peer.close()
