"""QUIC pacing のテスト

send() の確定書き出しで ngtcp2_conn_update_pkt_tx_time が呼ばれ、
pacing 期限が get_timeout() 経由で返ることを Sans-IO で検証する。
pacing 間隔は平滑化 RTT に比例するため、キュー満載のまま送受信を
間引いて RTT 標本を膨らませ、間隔を呼び出し overhead に対して十分
大きくしてから観測する。
"""

from __future__ import annotations

import time

from conftest import (
    CLIENT_ADDR,
    PUMP_ATTEMPTS,
    SERVER_ADDR,
    _drain_events,
    create_client_server_pair,
    perform_handshake,
    wait_pacing_timeout,
)

from webtransport.quic import Connection

# 送受信ラウンド間の待ち (秒)。RTT 標本を膨らませて pacing 間隔を
# 呼び出し overhead に対して十分大きくする
_TRICKLE_DELAY = 0.1
# 間引き送受信の回数
_TRICKLE_ROUNDS = 2
# 1 ラウンドで送受信する上限パケット数。増やしすぎると高速標本で
# 平滑化 RTT が潰れ、輻輳ウィンドウも膨らむため少なく保つ
_TRICKLE_PACKETS = 2


def _pump_some(client: Connection, server: Connection, max_packets: int) -> None:
    """両方向に最大パケット数まで送受信する。期限待ちも行う"""
    sent = 0
    for _ in range(max_packets * 2 + 10):
        if sent >= max_packets * 2:
            break
        server_packet = server.send()
        if server_packet is not None:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
            sent += 1
        client_packet = client.send()
        if client_packet is not None:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
            sent += 1
        if (
            server_packet is None
            and client_packet is None
            and not wait_pacing_timeout(client, server)
        ):
            break


def _prepare_paced() -> tuple[Connection, Connection]:
    """ハンドシェイクと pacing 有効化を済ませ、大量データをキューする

    大量データをキューしたまま送受信を間引いて RTT 標本を膨らませ、
    非 app-limited な ack で pacing 間隔を設定させる。

    Returns:
        クライアントとサーバーのタプル
    """
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)
    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, b"x" * 1048576, False)
    for _ in range(_TRICKLE_ROUNDS):
        _pump_some(client, server, _TRICKLE_PACKETS)
        # pacing 期限待ちの可能性があるため待って再試行する
        packet = None
        for _ in range(PUMP_ATTEMPTS):
            packet = client.send()
            if packet is not None:
                break
            if not wait_pacing_timeout(client, server):
                break
        assert packet is not None
        # 送信と配送の間隔が RTT 標本になる
        time.sleep(_TRICKLE_DELAY)
        server.receive(packet.data, SERVER_ADDR, CLIENT_ADDR)
    _pump_some(client, server, 1)
    _drain_events(client)
    _drain_events(server)
    return client, server


def _stall_timeout_with_room(client: Connection) -> int | None:
    """pacing 停滞を捉えて正の期限を返す。捉えられない時は None

    輻輳ウィンドウに空きがあるのに送出が止まった状態を捉える。更新
    呼び出しがなければ空きがある限り書けるため捉えられない。期限到来
    済み (0) の場合は取り直す。1 回の試行で消費するウィンドウは
    2 パケット以内に抑える
    """
    for _ in range(10):
        # 輻輳ウィンドウの空きがなければ観測不能のため打ち切る
        if client.bytes_in_flight + 2048 >= client.cwnd:
            return None
        if client.send() is not None and client.send() is not None:
            continue
        timeout = client.get_timeout()
        if timeout is not None and timeout > 0:
            return timeout
    return None


def _prepare_and_stall() -> tuple[Connection, int]:
    """準備と停滞捕捉を期限が得られるまで繰り返す

    間隔設定はタイミング依存のため、捉えられない回は接続を作り直す
    """
    timeout = None
    client = None
    for _ in range(3):
        client, _ = _prepare_paced()
        timeout = _stall_timeout_with_room(client)
        if timeout is not None:
            break
    assert timeout is not None
    assert client is not None
    return client, timeout


def test_confirmed_write_sets_pacing_deadline() -> None:
    """
    確定書き出しで pacing 期限が設定されることを確認する

    輻輳ウィンドウに空きがあるのに送出が止まり、未来の期限が返る。
    期限待ち後に ack なしで送出が再開する。
    """
    client, timeout = _prepare_and_stall()
    # 未来の期限である (アイドル期限 30 秒ではない)
    assert 0 < timeout < 1_000_000_000
    # 期限を過ぎると ack なしで送出が再開する。期限が 50 ms を超える場合に
    # 固定上限で待つと期限到達前に判定してしまうため、期限 + マージンまで待つ
    time.sleep(timeout / 1_000_000_000 + 0.005)
    assert client.send() is not None


def test_bulk_send_reports_near_deadline() -> None:
    """
    大量送信の停滞時に近い将来の期限が返ることを確認する

    送出が止まった直後の get_timeout() が近い将来の期限を返す。
    """
    _, timeout = _prepare_and_stall()
    # RTT 標本が膨らむと pacing 間隔も比例して膨らむため、CI ランナーの
    # 負荷で 1 秒を超えることがある。アイドル期限 (30 秒) と区別できる
    # 範囲で上限を設ける
    assert 0 <= timeout < 5_000_000_000
