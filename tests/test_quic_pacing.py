"""QUIC pacing のテスト

send() の確定書き出しで ngtcp2_conn_update_pkt_tx_time が呼ばれ、
pacing 期限が get_timeout() 経由で返ることを Sans-IO で検証する。
pacing 間隔は平滑化 RTT に比例するため、キュー満載のまま RTT 標本を
膨らませ、間隔を呼び出し overhead に対して十分大きくしてから観測する。

RTT 標本は、クライアントが送ったパケットの ACK をテスト側で保持し、
_ACK_HOLD_DELAY 秒だけ遅らせてからクライアントへ配送することで膨らませる。
ACK の生成自体を遅らせるとサーバーが申告する ACK delay も膨らみ、ngtcp2 は
標本から ACK delay を差し引くため膨らまない。生成は即座に済ませ、配送だけを
遅らせるのが要点である。
"""

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

# ACK の配送を遅らせる時間 (秒)。RTT 標本を膨らませて pacing 間隔を
# 呼び出し overhead に対して十分大きくする
_ACK_HOLD_DELAY = 0.1
# RTT 標本を膨らませる回数。2 回で平滑化 RTT は 20 ms 前後になる
_RTT_INFLATE_ROUNDS = 2
# 準備の最後に送受信する上限パケット数。高速な RTT 標本が平滑化 RTT を
# 潰し、輻輳ウィンドウも膨らむため少なく保つ
_PREPARE_PUMP_PACKETS = 1


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


def _deliver_ack_eliciting_packet(client: Connection, server: Connection) -> None:
    """クライアントの ack-eliciting なパケットを 1 つサーバーへ配送する

    サーバーに RTT 標本の対象となるパケットを 1 つだけ配送する。送信後に
    bytes_in_flight が増えるパケットを選ぶことで、ACK のみのパケット
    (in-flight に数えられず、受け取っても ACK が返らない) を除外する。

    Raises:
        AssertionError: ack-eliciting なパケットを送出できなかった場合
    """
    for _ in range(PUMP_ATTEMPTS):
        before_in_flight = client.bytes_in_flight
        packet = client.send()
        if packet is None:
            if not wait_pacing_timeout(client):
                break
            continue
        server.receive(packet.data, SERVER_ADDR, CLIENT_ADDR)
        if client.bytes_in_flight > before_in_flight:
            return
    raise AssertionError("ack-eliciting なパケットを送出できなかった")


def _inflate_rtt(client: Connection, server: Connection) -> None:
    """ACK の配送を遅らせて RTT 標本を膨らませる

    他にパケットを配送しないため、サーバーが生成する ACK は直前に配送した
    パケットのみを ack する。この ACK を _ACK_HOLD_DELAY 秒保持してから
    クライアントへ届けることで、標本は保持時間以上になる。

    Raises:
        AssertionError: RTT 標本を膨らませられなかった場合
    """
    _deliver_ack_eliciting_packet(client, server)
    for _ in range(10):
        before_rtt = client.latest_rtt
        ack = server.send()
        if ack is None:
            if not wait_pacing_timeout(server):
                break
            continue
        # ACK の生成は済ませたまま、クライアントへの配送だけを遅らせる
        time.sleep(_ACK_HOLD_DELAY)
        client.receive(ack.data, CLIENT_ADDR, SERVER_ADDR)
        if client.latest_rtt == before_rtt:
            continue
        # 保持時間の半分以上を観測できなければ、他のパケットの ACK に
        # 追い越されて標本が膨らまなかったと判断する
        assert client.latest_rtt >= _ACK_HOLD_DELAY * 1_000_000_000 * 0.5
        return
    raise AssertionError("RTT 標本を膨らませられなかった")


def _prepare_paced() -> tuple[Connection, Connection]:
    """ハンドシェイクと pacing 有効化を済ませ、大量データをキューする

    大量データをキューしたまま RTT 標本を膨らませ、非 app-limited な
    ack で pacing 間隔を設定させる。

    Returns:
        クライアントとサーバーのタプル
    """
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)
    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, b"x" * 1048576, False)
    for _ in range(_RTT_INFLATE_ROUNDS):
        _inflate_rtt(client, server)
    _pump_some(client, server, _PREPARE_PUMP_PACKETS)
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

    準備は決定的だが、環境によっては停滞を捉えられないことがあるため、
    捉えられない回は接続を作り直す
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
    # アイドル期限 (30 秒) と区別できる範囲で上限を設ける
    assert 0 <= timeout < 5_000_000_000
