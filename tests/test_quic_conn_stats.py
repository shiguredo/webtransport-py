"""QUIC 接続統計 API のテスト

ngtcp2_conn_info やフロー制御残量など、接続統計取得 API の動作を確認する。
"""

from conftest import (
    CLIENT_ADDR,
    SERVER_ADDR,
    create_client_server_pair,
    perform_handshake,
)

from webtransport.quic import Config, Connection

# ngtcp2 が RTT 未計測・スロー スタート開始時に返す初期値
UINT64_MAX = (1 << 64) - 1


def test_conn_stats_before_handshake():
    """ハンドシェイク前は初期値がそのまま返り None にならない"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.server_name = "localhost"
    client_config.verify_peer = False

    # send() 前の状態を確認するため、クライアントを直接生成する
    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)

    # ハンドシェイク前は ngtcp2 の初期値をそのまま返す
    assert client.latest_rtt == 0
    assert client.min_rtt == UINT64_MAX
    assert client.smoothed_rtt is not None
    assert client.rttvar is not None
    assert client.cwnd is not None
    assert client.ssthresh == UINT64_MAX
    assert client.bytes_in_flight == 0
    assert client.pkt_sent == 0
    assert client.bytes_sent == 0
    assert client.pkt_recv == 0
    assert client.bytes_recv == 0
    assert client.pkt_lost == 0
    assert client.bytes_lost == 0
    assert client.ping_recv == 0
    assert client.pkt_discarded == 0
    assert client.pto is not None
    assert client.cwnd_left == client.cwnd
    assert client.max_data_left is not None
    assert client.send_quantum is not None
    assert client.path_max_tx_udp_payload_size is not None


def test_conn_stats_after_handshake():
    """ハンドシェイク後は統計値が取得できる"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 実ストリームを開いてデータ送信前にスナップショットを取る
    stream_id = client.open_stream(True)
    assert stream_id >= 0
    max_data_left_before = client.max_data_left
    stream_max_data_left_before = client.max_stream_data_left(stream_id)

    # データを送信する
    payload = b"hello" * 100
    client.send_stream_data(stream_id, payload)

    for _ in range(5):
        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)

        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

    # データ送信でフロー制御残量が送信バイト数分だけ減る
    # (tx.offset は ACK に依存せず送信時に進むため決定論的。データ送信パスの証明)
    sent_bytes = len(payload)
    assert client.max_data_left == max_data_left_before - sent_bytes
    assert client.max_stream_data_left(stream_id) == stream_max_data_left_before - sent_bytes

    # 送受信カウンタも増加する
    # (ハンドシェイク中の ACK 交換でも増えるため、増加自体はデータ送信の直接の
    # 証明にはならない。データ送信の証明は上のフロー制御残量の減少が担う)
    assert client.pkt_sent > 0
    assert client.bytes_sent > 0
    assert server.pkt_recv > 0
    assert server.bytes_recv > 0

    # ハンドシェイク中の ACK 交換で RTT サンプルが計測される
    # (RTT サンプルは send と ACK 受信の実時間差分で計測される。latest_rtt は
    # サンプルが無いと 0、min_rtt は UINT64_MAX のままのため、正値は RTT 計測
    # の証明になる。smoothed_rtt / rttvar は初期値 (設定 RTT とその半分) でも
    # 正の値を返す)
    assert client.latest_rtt > 0
    assert client.min_rtt > 0
    assert client.smoothed_rtt > 0
    assert client.rttvar > 0
    assert client.pto > 0

    # サーバー側でも RTT・フロー制御の統計が取得できる
    assert server.latest_rtt > 0
    assert server.min_rtt > 0
    assert server.smoothed_rtt > 0
    assert server.pto > 0
    assert server.max_stream_data_left(stream_id) > 0


def test_conn_stats_nonexistent_stream():
    """存在しないストリームの統計は 0 を返す"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 存在しない・負の・巨大なストリーム ID にはクラッシュせず 0 が返る
    assert client.max_stream_data_left(9999) == 0
    assert client.stream_loss_count(9999) == 0
    assert client.max_stream_data_left(-1) == 0
    assert client.stream_loss_count(-1) == 0
    assert client.max_stream_data_left(1 << 62) == 0
    assert client.stream_loss_count(1 << 62) == 0


def test_conn_stats_after_close():
    """接続を閉じた後は統計が None を返す"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 接続を閉じる
    client.close(0, "normal close")
    assert client.is_closed()

    assert client.latest_rtt is None
    assert client.min_rtt is None
    assert client.smoothed_rtt is None
    assert client.rttvar is None
    assert client.cwnd is None
    assert client.ssthresh is None
    assert client.bytes_in_flight is None
    assert client.pkt_sent is None
    assert client.bytes_sent is None
    assert client.pkt_recv is None
    assert client.bytes_recv is None
    assert client.pkt_lost is None
    assert client.bytes_lost is None
    assert client.ping_recv is None
    assert client.pkt_discarded is None
    assert client.pto is None
    assert client.cwnd_left is None
    assert client.max_data_left is None
    assert client.max_stream_data_left(0) is None
    assert client.stream_loss_count(0) is None
    assert client.send_quantum is None
    assert client.path_max_tx_udp_payload_size is None
