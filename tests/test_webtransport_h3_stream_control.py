"""WebTransport over HTTP/3 のストリーム・接続制御 API テスト"""

from __future__ import annotations

from webtransport import h3


def _pump(src: h3.Session, dst: h3.Session) -> None:
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


def _establish_session() -> tuple[h3.Session, h3.Session, int]:
    """h3.Session 同士で WebTransport セッションを確立する

    @return (クライアント Session, サーバー Session, セッション ID)
    """
    client = h3.Session.create_client(h3.Config())
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)

    # ストリームをバインド (クライアントの単方向ストリームは %4 == 2、
    # サーバーは %4 == 3)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)
    server.set_max_client_streams_bidi(100)

    # サーバーの SETTINGS をクライアントに送る
    _pump(server, client)

    # クライアントが CONNECT を送信 (クライアント起動双方向ストリームは %4 == 0)
    assert client.connect(0, "https://localhost/webtransport") is True
    _pump(client, server)

    # サーバーがセッションを受理
    session_id = -1
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_READY:
            assert server.accept_session(event.session_id) is True
            session_id = event.session_id
    assert session_id >= 0
    _pump(server, client)

    # クライアント側の SESSION_READY を確認
    ready_id = -1
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_READY:
            ready_id = event.session_id
    assert ready_id == session_id

    return client, server, session_id


def test_block_unblock_stream() -> None:
    """ストリームのブロック / アンブロックで送信が止まることを確認"""
    client, server, session_id = _establish_session()

    # データストリームを開いてデータを送信する (block 前に flush して
    # しまうと unblock 後も再スケジュールされないため、flush しない)
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"hello")
    assert client.stream_writable(stream_id) == 1

    # block するとスケジューラから外れ、データが出ず書き込み不可になる
    client.block_stream(stream_id)
    assert client.stream_writable(stream_id) == 0
    assert client.get_streams_to_send() == []

    # unblock すると再スケジュールされ、データが出て書き込み可能になる
    assert client.unblock_stream(stream_id) is True
    assert client.stream_writable(stream_id) == 1

    # 取り出したデータをそのままピアに渡す (get_streams_to_send は
    # 取り出した時点で消費されるため)。データが空になるまで繰り返す
    sent_data = False
    for _ in range(64):
        streams = client.get_streams_to_send()
        if not streams:
            break
        for sid, data, fin in streams:
            if sid == stream_id:
                sent_data = True
            server.receive_stream_data(sid, data, fin)
    assert sent_data, "unblock 後にデータが再出しません"

    # ピアにデータが届く
    received = False
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == h3.EventType.STREAM_DATA:
            assert event.stream_id == stream_id
            assert event.data == b"hello"
            received = True
    assert received, "データが届きません"


def test_max_concurrent_streams() -> None:
    """同時ストリーム数のヒントを設定してもセッション確立と送受信が継続できることを確認

    効果は外部から観測できない (現在値との max マージのため) ため、
    呼び出し後も通常の動作が続くことのみ確認する
    """
    client, server, session_id = _establish_session()

    # ヒントを設定する
    client.max_concurrent_streams(10)
    server.max_concurrent_streams(10)

    # ヒント設定後もデータストリームの送受信が継続できる
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"hello")
    _pump(client, server)

    received = False
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == h3.EventType.STREAM_DATA:
            assert event.data == b"hello"
            received = True
    assert received, "データが届きません"


def test_block_unblock_guards() -> None:
    """存在しない・範囲外のストリームでは no-op / 成功になることを確認"""
    client, _server, _session_id = _establish_session()

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
