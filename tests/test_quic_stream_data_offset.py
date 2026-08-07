"""QUIC STREAM_DATA イベントのオフセットテスト

受信したストリームデータの絶対位置 (offset) が Event から取得できることを
確認する。
"""

from conftest import (
    CLIENT_ADDR,
    SERVER_ADDR,
    create_client_server_pair,
    perform_handshake,
)

from webtransport import quic


def test_stream_data_offset_matches_cumulative_position():
    """STREAM_DATA イベントの offset が受信データの累積位置と一致することを確認

    データを複数チャンクに分けて送信し、受信イベントの offset が「前の
    イベントの offset + データ長」と一致することを確認する。ngtcp2 の契約は
    「offset の非減少順・重複なしで渡す」ことのみであり (ngtcp2.h の
    ngtcp2_recv_stream_data)、連続配送 (gap なし) は本実装の受信経路
    (ngtcp2 の reorder buffer) の挙動に依存する。ロスなしのテスト環境では
    連続配送されるため、offset の累積で受信位置を追跡できる
    """
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    stream_id = client.open_stream(True)
    assert stream_id >= 0

    # 複数チャンクに分けて送信する (初期受信ウィンドウ (256 KiB) 内のデータ量)
    chunks = [b"a" * 1000, b"b" * 1000, b"c" * 1000, b"d" * 1000, b"e" * 1000]
    for chunk in chunks:
        client.send_stream_data(stream_id, chunk)

    # パケットを交換してサーバーに届ける
    for _ in range(100):
        client_packet = client.send()
        if client_packet is not None:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
        server_packet = server.send()
        if server_packet is not None:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

    # サーバーの STREAM_DATA イベントを収集する
    received = []
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == quic.EventType.STREAM_DATA:
            received.append((event.offset, len(event.data)))

    # 各イベントの offset が累積位置と一致する (連続配送の検証)
    assert received, "STREAM_DATA イベントが受信されていません"
    expected_offset = 0
    for offset, data_len in received:
        assert offset == expected_offset
        expected_offset += data_len
    # 全チャンクが欠落なく届いている
    assert expected_offset == sum(len(chunk) for chunk in chunks)
