"""QUIC ストリームのロス時再送の完全性テスト

パケットロス時の再送でストリームデータが破損しないことを検証する。
送出バッファを書き出し時に解放すると、再送が解放済みメモリを再読して
ずれた内容を送る (ngtcp2 の保持契約違反)。Sans-IO で c2s パケットを
規則的に落としても内容一致することを決定的に検証する。
"""

from __future__ import annotations

import time

import pytest
from conftest import CLIENT_ADDR, SERVER_ADDR, create_client_server_pair, perform_handshake

from webtransport import quic

# QUIC パケット境界をまたぐサイズを選ぶ
PAYLOAD = bytes((index % 256) for index in range(32 * 1024))

# 完了待ちの壁時計デッドライン (秒)。pytest-timeout=30 の内側に収める
COMPLETION_DEADLINE = 20.0


def _exchange_with_loss(
    client: quic.Connection,
    server: quic.Connection,
    stream_id: int,
    drop_mod: int,
    drop_rem: int,
) -> bytes:
    """c2s パケットを規則的に 1 回だけ落として転送し、受信バイト列を返す

    落としたパケットは再送で回復する。ACK 駆動の高速再送に加え、PTO 発火の
    ためタイマーを実時間で進める。ACK 方向 (s2c) は全通しする。完了条件
    (FIN 到達かつ全長受信) に達するまでポンプし、デッドライン超過時は
    受信済み内容で打ち切る (呼び出し側が長さと内容を検証する)。

    Args:
        client: 送信側のクライアント接続
        server: 受信側のサーバー接続
        stream_id: 検証対象のストリーム ID
        drop_mod: 落とす周期
        drop_rem: 落とす剰余

    Returns:
        サーバーが受信したバイト列
    """
    c2s_count = 0
    dropped_once: set[int] = set()
    received = bytearray()
    deadline = time.monotonic() + COMPLETION_DEADLINE
    while True:
        # クライアントからサーバーへ (ドロップ付き、初回のみ)
        while True:
            packet = client.send()
            if packet is None:
                break
            index = c2s_count
            c2s_count += 1
            if index % drop_mod == drop_rem and index not in dropped_once:
                dropped_once.add(index)
                continue
            server.receive(packet.data, SERVER_ADDR, CLIENT_ADDR)
        # サーバーからクライアントへ (全通し)
        while True:
            packet = server.send()
            if packet is None:
                break
            client.receive(packet.data, CLIENT_ADDR, SERVER_ADDR)
        # タイマーを処理して再送を誘発する。PTO は実時刻で満了するため、
        # 進捗が無ければ短く待って時刻を進める
        made_progress = False
        for connection, peer, peer_local, peer_remote in (
            (server, client, CLIENT_ADDR, SERVER_ADDR),
            (client, server, SERVER_ADDR, CLIENT_ADDR),
        ):
            if connection.get_timeout() is not None:
                connection.handle_timeout()
                made_progress = True
                while True:
                    packet = connection.send()
                    if packet is None:
                        break
                    if connection is client:
                        index = c2s_count
                        c2s_count += 1
                        if index % drop_mod == drop_rem and index not in dropped_once:
                            dropped_once.add(index)
                            continue
                    peer.receive(packet.data, peer_local, peer_remote)
        # 受信イベントを取り出す
        fin_seen = False
        while True:
            event = server.next_event()
            if event is None:
                break
            if event.type == quic.EventType.STREAM_DATA and event.stream_id == stream_id:
                received.extend(event.data)
                fin_seen = fin_seen or event.fin
        if fin_seen and len(received) >= len(PAYLOAD):
            break
        if time.monotonic() > deadline:
            break
        if not made_progress:
            time.sleep(0.01)
    return bytes(received)


@pytest.mark.parametrize(
    "drop_mod,drop_rem",
    [(3, 0), (3, 1), (3, 2)],
    ids=["drop-0-3", "drop-1-3", "drop-2-3"],
)
def test_stream_retransmit_keeps_content(drop_mod: int, drop_rem: int) -> None:
    """ロス時の再送でもストリーム内容が一致することを確認する

    c2s パケットを 3 ごとに 1 枚落としても、再送で回復した内容が
    送信バイト列と完全一致することを確認する。
    """
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, PAYLOAD, True)

    received = _exchange_with_loss(client, server, stream_id, drop_mod, drop_rem)
    assert len(received) == len(PAYLOAD)
    assert received == PAYLOAD


def test_stream_transfer_without_loss() -> None:
    """無ドロップ対照でストリーム内容が一致することを確認する

    ロス注入ヘルパー自体の正当性を担保する対照実験である。剰余は
    `drop_mod` 未満になり得ない値を渡し、1 枚も落とさない。
    """
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, PAYLOAD, True)

    received = _exchange_with_loss(client, server, stream_id, 1_000_000, 1_000_000)
    assert len(received) == len(PAYLOAD)
    assert received == PAYLOAD
