"""QUIC 受信フロー制御の再開放テスト

recv_stream_data_cb が受信したデータ量ぶんのストリーム・コネクション両方の
フロー制御を再開放し、初期受信ウィンドウを超えるデータ転送が止まらずに
完了することを確認する。
"""

import time

from conftest import (
    CLIENT_ADDR,
    SERVER_ADDR,
    create_client_server_pair,
    perform_handshake,
)

from webtransport import quic

# ストリーム / コネクションの初期受信ウィンドウ (QuicConfig のデフォルト値)
STREAM_INITIAL_WINDOW = 262144
CONNECTION_INITIAL_WINDOW = 1048576


def exchange_until_all_received(
    client: quic.Connection,
    server: quic.Connection,
    expected_bytes: int,
) -> int:
    """サーバーが expected_bytes を受信するまでパケットを交換する

    クライアントは送れるだけ送り、サーバーは受信への応答 (MAX_STREAM_DATA /
    MAX_DATA / ACK) を送れるだけ送る。Sans-IO では時間が明示的に進まない
    ため、ACK 遅延等のタイマーは get_timeout() で期限を確認し、
    handle_timeout() で処理する。期限がまだ来ていないタイマー (ACK 遅延等、
    1 秒未満) は期限まで実時間を進めてから処理する。これは CI 環境のように
    パケット交換ループの実行時間が ACK 遅延 (25ms) を下回る場合、タイマーが
    満了せず ACK が送出されず、クライアントの送信が cwnd でブロックされて
    転送が進まないためである。
    ループ上限 3000 は、1.2 MiB の転送が実測で数十ラウンドで完了することに
    対して十分な余裕を持つ。

    Returns:
        サーバーが受信したバイト数
    """
    server_received = 0
    for _ in range(3000):
        # クライアントは送れるだけ送る (cwnd / 送信ウィンドウが枯渇するまで)
        while True:
            client_packet = client.send()
            if client_packet is None:
                break
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
            while True:
                event = server.next_event()
                if event is None:
                    break
                if event.type == quic.EventType.STREAM_DATA:
                    server_received += len(event.data)

        # サーバーは送れるだけ送る (MAX_STREAM_DATA / MAX_DATA / ACK)
        while True:
            server_packet = server.send()
            if server_packet is None:
                break
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

        # ACK 遅延等のタイマーを消化する。期限が 1 秒未満のタイマー (ACK 遅延
        # 等) は期限まで実時間を進めてから handle_timeout() を呼ぶ。これにより
        # ループの実行速度に関係なくタイマーを確実に満了させ、ACK の送出を
        # 保証する。
        for connection, peer, local_addr, remote_addr in (
            (server, client, SERVER_ADDR, CLIENT_ADDR),
            (client, server, CLIENT_ADDR, SERVER_ADDR),
        ):
            timeout = connection.get_timeout()
            if timeout is None:
                continue
            if timeout > 0 and timeout < 1_000_000_000:
                time.sleep(timeout / 1_000_000_000)
            connection.handle_timeout()
            packet = connection.send()
            if packet is not None:
                peer.receive(packet.data, local_addr, remote_addr)
                # タイマー消化で送信されたデータが運ぶイベントを集計する
                # (受信側は peer のため、peer のキューを drain する)
                if peer is server:
                    while True:
                        event = server.next_event()
                        if event is None:
                            break
                        if event.type == quic.EventType.STREAM_DATA:
                            server_received += len(event.data)

        if server_received >= expected_bytes:
            break
    return server_received


def test_recv_flow_control_reopens_stream_and_connection():
    """受信フロー制御の再開放を確認する

    サーバーの初期受信ウィンドウ (ストリーム 256 KiB / コネクション 1 MiB)
    を超えるデータを送信し、サーバーが全量を受信できることを確認する。
    再開放が無ければサーバーの受信ウィンドウでクライアントの送信が
    ブロックされ、全量が届かない (RFC 9000 Section 4.1 の MUST)。全量の
    受信はストリームレベルとコネクションレベルの両方の再開放が機能して
    初めて成立する。
    """
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    stream_id = client.open_stream(True)
    assert stream_id >= 0

    # ストリーム (256 KiB) とコネクション (1 MiB) の初期受信ウィンドウを
    # 両方超える 1.2 MiB を送信する
    # (MAX_STREAM_DATA / MAX_DATA の送出閾値 (window/4) も超える)
    payload = b"a" * (1200 * 1024)
    client.send_stream_data(stream_id, payload)

    server_received = exchange_until_all_received(client, server, len(payload))

    # 再開放が機能し、サーバーが初期受信ウィンドウを超えて全量を受信できた
    assert server_received == len(payload)
    assert len(payload) > STREAM_INITIAL_WINDOW
    assert len(payload) > CONNECTION_INITIAL_WINDOW

    # クライアントの送信ウィンドウも再開放 (MAX_STREAM_DATA / MAX_DATA) で
    # 拡張され、ブロックされずに送信を完了できた
    # (再開放が無ければ送信ウィンドウが枯渇して送信が進まない)
    assert client.max_stream_data_left(stream_id) > 0
    assert client.max_data_left > 0
