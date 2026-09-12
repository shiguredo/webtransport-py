"""QUIC の STOP_SENDING 伝播テスト

ピアからの STOP_SENDING (RFC 9000 Section 19.5) が低レベル `quic.Event` の
`STOP_SENDING` として伝播し、アプリケーションエラーコードを観測できることを
検証する。draft-ietf-webtrans-http3-16 Section 4.4 は「WebTransport 実装は
これらのシグナルをアプリケーションへ伝播する」と定めており、RESET_STREAM
だけを伝播して STOP_SENDING を落とすと仕様違反になる。

実際に STOP_SENDING を送出させるため、Sans-IO 層でハンドシェイクとストリーム
開設を行い、送信側 (`close_stream`) から送出する。
"""

from __future__ import annotations

import asyncio

import pytest
from conftest import (
    CLIENT_ADDR,
    PUMP_ATTEMPTS,
    SERVER_ADDR,
    create_client_server_pair,
    perform_handshake,
    wait_pacing_timeout,
)

from webtransport.quic import Client, EventType, Server


def _pump_until_stop_sending(
    client: object,
    server: object,
    stream_id: int,
    error_code: int,
) -> list[object]:
    """サーバーが STOP_SENDING を送出し、クライアントが観測するまでポンプする

    next_event() は取り出したイベントをキューから消すため、観測までに
    取り出したイベントを返す (呼び出し側で検証に使う)。

    Args:
        client: クライアント Connection
        server: サーバー Connection
        stream_id: STOP_SENDING の対象ストリーム
        error_code: アプリケーションエラーコード

    Returns:
        ポンプ中にクライアントが取り出したイベント列
    """
    server.close_stream(stream_id, error_code)

    observed: list[object] = []
    for _ in range(PUMP_ATTEMPTS):
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)

        observed.extend(_drain(client))
        if any(
            event.type == EventType.STOP_SENDING and event.stream_id == stream_id
            for event in observed
        ):
            return observed

        if (
            server_packet is None
            and client_packet is None
            and not wait_pacing_timeout(client, server)
        ):
            break

    raise AssertionError("STOP_SENDING を観測できませんでした")


def _drain(connection: object) -> list[object]:
    """next_event() が None を返すまでイベントを取り出す"""
    events = []
    while True:
        event = connection.next_event()
        if event is None:
            return events
        events.append(event)


def test_stop_sending_event_propagates_error_code():
    """ピアの STOP_SENDING が STOP_SENDING イベントとして届くことを確認する

    サーバーがクライアント起動の双方向ストリームへ STOP_SENDING を送ると、
    クライアントは stream_id とアプリケーションエラーコードを伴う
    `EventType.STOP_SENDING` イベントを観測する。
    """
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet) is True

    stream_id = client.open_stream(bidirectional=True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, b"hello", fin=False)

    # サーバーへストリームのデータを届けてから STOP_SENDING を送る
    for _ in range(PUMP_ATTEMPTS):
        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
        if not client_packet and not server_packet and not wait_pacing_timeout(client, server):
            break

    error_code = 0x1234
    events = _pump_until_stop_sending(client, server, stream_id, error_code)

    stop_sending_events = [event for event in events if event.type == EventType.STOP_SENDING]
    assert len(stop_sending_events) >= 1
    assert stop_sending_events[-1].stream_id == stream_id
    assert stop_sending_events[-1].error_code == error_code


def test_stop_sending_event_exposes_stream_id():
    """STOP_SENDING イベントが対象ストリーム ID を保持することを確認する

    複数ストリームを開き、片方だけを停止した場合に、停止したストリーム ID
    のみがイベントとして届く。
    """
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet) is True

    first_stream_id = client.open_stream(bidirectional=True)
    second_stream_id = client.open_stream(bidirectional=True)
    assert first_stream_id != second_stream_id
    client.send_stream_data(first_stream_id, b"first", fin=False)
    client.send_stream_data(second_stream_id, b"second", fin=False)

    for _ in range(PUMP_ATTEMPTS):
        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
        if not client_packet and not server_packet and not wait_pacing_timeout(client, server):
            break

    # 1 本目だけを停止する
    events = _pump_until_stop_sending(client, server, first_stream_id, 7)

    stop_sending_ids = [event.stream_id for event in events if event.type == EventType.STOP_SENDING]
    assert first_stream_id in stop_sending_ids
    assert second_stream_id not in stop_sending_ids


async def _run_server(server: Server) -> None:
    """サーバーのメインループを実行する (キャンセルで終了)"""
    try:
        await server.run()
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_high_level_server_on_stop_sending(test_certificates):
    """高レベル Server の on_stop_sending がピアの STOP_SENDING で発火することを確認する

    サーバーが双方向ストリームを開いてデータを送り、クライアントがその
    ストリームを shutdown_stream して STOP_SENDING を送出する。サーバー側で
    stream_id とアプリケーションエラーコードが観測できる。
    """
    received: list[tuple[int, int]] = []
    stop_sending_received = asyncio.Event()
    server_stream_id: list[int] = []

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_handshake_completed(addr: tuple[str, int]) -> None:
        # サーバー起動の双方向ストリームを開き、データを送って書き込み可能にする
        stream_id = await server.open_stream(addr, bidirectional=True)
        assert stream_id >= 0
        server_stream_id.append(stream_id)
        await server.send_stream_data(addr, stream_id, b"server-data", fin=False)

    async def on_stop_sending(stream_id: int, error_code: int, addr: tuple[str, int]) -> None:
        received.append((stream_id, error_code))
        stop_sending_received.set()

    server.on_handshake_completed(on_handshake_completed)
    server.on_stop_sending(on_stop_sending)
    await server.start()

    server_task = asyncio.create_task(_run_server(server))

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    client_received: list[int] = []
    client_data_received = asyncio.Event()

    async def on_client_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        client_received.append(stream_id)
        client_data_received.set()

    client.on_stream_data(on_client_stream_data)
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    try:
        # サーバー起動のストリームが届くまで待つ
        await asyncio.wait_for(client_data_received.wait(), timeout=5.0)
        assert server_stream_id, "サーバーがストリームを開けませんでした"
        stream_id = client_received[0]
        assert stream_id == server_stream_id[0]

        # クライアントが受信側を停止し、STOP_SENDING を送出する
        error_code = 0x2A
        await client.shutdown_stream(stream_id, error_code=error_code)

        await asyncio.wait_for(stop_sending_received.wait(), timeout=5.0)
        assert received == [(stream_id, error_code)]
    finally:
        server_task.cancel()
        await asyncio.gather(server_task, return_exceptions=True)
        await client.close()
        await server.stop()
