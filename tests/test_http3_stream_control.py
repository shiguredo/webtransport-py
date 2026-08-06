"""HTTP/3 のストリーム・接続制御 API テスト"""

from __future__ import annotations

from webtransport import http3


def _pump(src: http3.Connection, dst: http3.Connection) -> None:
    """src の送信データを全て dst に渡す

    QUIC レイヤーを介さず、get_streams_to_send で取り出したデータを
    receive_stream_data で直接渡す (モックなし)。get_streams_to_send は
    1 回の呼び出しで全てのデータを返すとは限らないため、データが無く
    なるまで繰り返す
    """
    for _ in range(64):
        sent = False
        for stream_id, data, fin in src.get_streams_to_send():
            dst.receive_stream_data(stream_id, data, fin)
            sent = True
        if not sent:
            break


def _create_connection_pair() -> tuple[http3.Connection, http3.Connection]:
    """Http3Connection のクライアント・サーバーペアを作成して初期化する

    @return (クライアント Connection, サーバー Connection)
    """
    client = http3.Connection.create_client(http3.Config())
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)

    # ストリームをバインド (クライアントの単方向ストリームは %4 == 2、
    # サーバーは %4 == 3)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)

    # 双方の SETTINGS を交換する
    _pump(server, client)
    _pump(client, server)

    return client, server


def _request_headers() -> list[tuple[str, str]]:
    """テスト用のリクエストヘッダー"""
    return [
        (":method", "GET"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]


def test_http3_block_unblock_stream() -> None:
    """ストリームのブロック / アンブロックで送信が止まることを確認"""
    client, server = _create_connection_pair()

    # リクエストを送信してデータを積む (block 前に flush してしまうと
    # unblock 後も再スケジュールされないため、flush しない)
    assert client.submit_request(0, _request_headers()) is True
    client.send_data(0, b"request-body", fin=False)
    assert client.stream_writable(0) == 1

    # block するとスケジューラから外れ、データが出ず書き込み不可になる
    client.block_stream(0)
    assert client.stream_writable(0) == 0
    assert client.get_streams_to_send() == []

    # unblock すると再スケジュールされ、データが出て書き込み可能になる
    assert client.unblock_stream(0) is True
    assert client.stream_writable(0) == 1

    # 取り出したデータをそのままピアに渡す (get_streams_to_send は
    # 取り出した時点で消費されるため)。データが空になるまで繰り返す
    sent_data = False
    for _ in range(64):
        streams = client.get_streams_to_send()
        if not streams:
            break
        for sid, data, fin in streams:
            if sid == 0:
                sent_data = True
            server.receive_stream_data(sid, data, fin)
    assert sent_data, "unblock 後にデータが再出しません"

    # ピアにヘッダーとボディが届く
    received_headers = False
    received_body = False
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == http3.EventType.HEADERS:
            assert event.stream_id == 0
            received_headers = True
        if event.type == http3.EventType.DATA:
            assert event.stream_id == 0
            assert event.data == b"request-body"
            received_body = True
    assert received_headers, "ヘッダーが届きません"
    assert received_body, "ボディが届きません"


def test_http3_max_concurrent_streams() -> None:
    """同時ストリーム数のヒントを設定しても通常の送受信が継続できることを確認

    効果は外部から観測できない (現在値との max マージのため) ため、
    呼び出し後も通常の動作が続くことのみ確認する
    """
    client, server = _create_connection_pair()

    # ヒントを設定する
    client.max_concurrent_streams(10)
    server.max_concurrent_streams(10)

    # ヒント設定後もリクエスト送受信が継続できる
    assert client.submit_request(0, _request_headers()) is True
    client.send_data(0, b"request-body", fin=True)
    _pump(client, server)

    assert server.submit_response(0, [(":status", "200")]) is True
    server.send_data(0, b"response-body", fin=True)
    _pump(server, client)

    received = False
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type == http3.EventType.DATA:
            assert event.data == b"response-body"
            received = True
    assert received, "データが届きません"


def test_http3_block_unblock_guards() -> None:
    """存在しない・範囲外のストリームでは no-op / 成功になることを確認"""
    client, _server = _create_connection_pair()

    # 存在しないストリームの unblock_stream は成功扱い (nghttp3 が 0 を返す)
    assert client.unblock_stream(999) is True
    # 存在しないストリームの block_stream は no-op (例外なし)
    client.block_stream(999)
    # 負の値と NGHTTP3_MAX_VARINT (2**62 - 1) を超える値も nghttp3 が
    # assert なしで扱うため安全 (クラッシュしないことを確認する)
    assert client.unblock_stream(-1) is True
    client.block_stream(-1)
    assert client.unblock_stream(1 << 62) is True
    client.block_stream(1 << 62)

    # 「コネクションが無い場合の no-op / False」は公開 API から conn_ を
    # 破棄する手段が無くモックも禁止のためテスト不能 (防御的ガードのみ)
