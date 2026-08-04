"""WebTransport over HTTP/3 のストリーム状態確認 API テスト"""

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


def test_stream_writable() -> None:
    """データストリームの書き込み可否が取得できることを確認"""
    client, _server, session_id = _establish_session()

    # データストリームを開くと書き込み可能になる
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    assert client.stream_writable(stream_id) == 1

    # 存在しないストリームは書き込み不可
    assert client.stream_writable(999) == 0


def test_stream_flushed() -> None:
    """送信データが QUIC スタックに受け渡し済みか確認できることを確認"""
    client, server, session_id = _establish_session()

    # データストリームを開いてデータを送信する
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"hello")

    # 送信処理前は QUIC スタックに受け渡し済みでない
    assert client.stream_flushed(stream_id) == 0

    # 送信処理で QUIC スタックに受け渡すと受け渡し済みになる
    _pump(client, server)
    assert client.stream_flushed(stream_id) == 1

    # 存在しないストリームは受け渡し済み扱い (1) になる
    assert client.stream_flushed(999) == 1


def test_stream_wt_session_id() -> None:
    """ストリームが属する WebTransport セッション ID が取得できることを確認"""
    client, _server, session_id = _establish_session()

    # データストリームのセッション ID が取得できる
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    assert client.stream_wt_session_id(stream_id) == session_id

    # 存在しないストリームは None
    assert client.stream_wt_session_id(999) is None

    # WebTransport データストリームでないストリームもセッション ID を持たない
    # ため None。CONNECT ストリーム自身 (セッション ID は CONNECT ストリーム
    # ID そのもの。draft-ietf-webtrans-http3-16 Section 2.2) で検証する
    assert client.stream_wt_session_id(session_id) is None
