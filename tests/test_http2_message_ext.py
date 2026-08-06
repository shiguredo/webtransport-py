"""HTTP/2 のメッセージング拡張 API テスト"""

from __future__ import annotations

from webtransport import http2


def _create_connection_pair() -> tuple[http2.Connection, http2.Connection]:
    """クライアントとサーバーのペアを作成して SETTINGS を交換する

    @return (クライアント Connection, サーバー Connection)
    """
    client = http2.Connection.create_client(http2.Config())
    server_config = http2.Config()
    server_config.is_server = True
    server = http2.Connection.create_server(server_config)
    _exchange_settings(client, server)
    return client, server


def _exchange_settings(client: http2.Connection, server: http2.Connection) -> None:
    """SETTINGS フレームを交換してセッションを確立する

    双方の送信データが無くなるまで送信と受信を繰り返す
    """
    for _ in range(10):
        client_data = client.send()
        if client_data:
            server.receive(client_data)

        server_data = server.send()
        if server_data:
            client.receive(server_data)

        if not client_data and not server_data:
            break


def _pump(src: http2.Connection, dst: http2.Connection) -> None:
    """src の送信データを全て dst に渡す

    send() は 1 回の呼び出しでフレームが無くなるまで返すとは限らない
    ため、送信データが無くなるまで繰り返す
    """
    for _ in range(10):
        data = src.send()
        if data:
            dst.receive(data)
        if not data:
            break


def _request_headers() -> list[tuple[str, str]]:
    """テスト用のリクエストヘッダー"""
    return [
        (":method", "GET"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]


def _drain_events(conn: http2.Connection) -> list[http2.Event]:
    """コネクションのイベントを全て取り出す"""
    events = []
    while True:
        event = conn.next_event()
        if event is None:
            break
        events.append(event)
    return events


def test_http2_submit_trailer() -> None:
    """サーバーがレスポンスの後にトレーラを送信できることを確認"""
    client, server = _create_connection_pair()

    # クライアントがリクエストを送信する
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    # サーバーがレスポンス + DATA + トレーラを送信する (DATA は eof=False
    # で積み、トレーラ HEADERS が END_STREAM を担う)
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"response-body", False)
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is True
    _pump(server, client)

    # クライアントでレスポンス HEADERS → DATA → トレーラ HEADERS → の順に
    # イベントが届く
    events = _drain_events(client)
    response_index = next(
        i
        for i, e in enumerate(events)
        if e.type == http2.EventType.HEADERS and (":status", "200") in e.headers
    )
    data_indices = [
        i
        for i, e in enumerate(events)
        if e.type == http2.EventType.DATA and e.stream_id == stream_id
    ]
    assert data_indices, "DATA イベントがありません"
    # DATA の内容を確認する
    assert b"".join(events[i].data for i in data_indices) == b"response-body"
    trailer_index = next(
        i
        for i, e in enumerate(events)
        if e.type == http2.EventType.HEADERS
        and ("x-trailer", "value") in e.headers
    )
    assert response_index < data_indices[0] < trailer_index
    # トレーラ HEADERS が END_STREAM を担うため、STREAM_END はトレーラの
    # 後に届く
    stream_end_index = next(
        i
        for i, e in enumerate(events)
        if e.type == http2.EventType.STREAM_END and e.stream_id == stream_id
    )
    assert stream_end_index > trailer_index


def test_http2_submit_trailer_after_flush() -> None:
    """送信データを flush した後でもトレーラを送信できることを確認"""
    client, server = _create_connection_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"response-body", False)
    _pump(server, client)

    # DATA 送出後にトレーラを予約しても、deferred 状態の再開により送信される
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is True
    _pump(server, client)

    events = _drain_events(client)
    assert any(
        e.type == http2.EventType.HEADERS
        and ("x-trailer", "value") in e.headers
        for e in events
    )
    assert any(
        e.type == http2.EventType.STREAM_END and e.stream_id == stream_id
        for e in events
    )


def test_http2_submit_trailer_eof_data() -> None:
    """eof=True のデータが積まれている場合はトレーラを送信できないことを確認"""
    client, server = _create_connection_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    # eof=True のデータを積むと END_STREAM 付き DATA になるため、その後に
    # トレーラを送信できない (RFC 9113 8.1 節)
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"response-body", True)
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is False

    # トレーラなしで flush すると END_STREAM 付きで終端する
    _pump(server, client)
    events = _drain_events(client)
    assert not any(
        e.type == http2.EventType.HEADERS
        and ("x-trailer", "value") in e.headers
        for e in events
    )
    assert any(
        e.type == http2.EventType.STREAM_END and e.stream_id == stream_id
        for e in events
    )

    # flush 済み (ローカル側 half-closed) のストリームにも送信できない
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is False


def test_http2_submit_trailer_reset_stream() -> None:
    """ストリームをリセットすると保留中のトレーラが送信されないことを確認"""
    client, server = _create_connection_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"body", False)
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is True

    # トレーラ送信前にストリームをリセットする
    server.reset_stream(stream_id)
    _pump(server, client)

    events = _drain_events(client)
    assert not any(
        e.type == http2.EventType.HEADERS
        and ("x-trailer", "value") in e.headers
        for e in events
    )
    assert any(e.type == http2.EventType.STREAM_RESET for e in events)


def test_http2_submit_trailer_after_eof_data() -> None:
    """トレーラ予約後に eof=True のデータを積んでもトレーラが送信されることを確認

    トレーラ HEADERS が END_STREAM を担うため、予約済みストリームの
    eof=True は無効化される
    """
    client, server = _create_connection_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    server.submit_response(stream_id, [(":status", "200")])
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is True
    # eof=True でもトレーラが END_STREAM を担うため、eof は無効化される
    server.send_data(stream_id, b"response-body", True)
    _pump(server, client)

    events = _drain_events(client)
    assert any(
        e.type == http2.EventType.HEADERS
        and ("x-trailer", "value") in e.headers
        for e in events
    )
    assert any(
        e.type == http2.EventType.STREAM_END and e.stream_id == stream_id
        for e in events
    )


def test_http2_submit_priority_update() -> None:
    """クライアントが PRIORITY_UPDATE フレームを送信できることを確認"""
    client, server = _create_connection_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    # 優先度を更新する (incremental 付き)。連続送信で優先度の再更新
    # (RFC 9218 の再更新) ができることも確認する
    assert client.submit_priority_update(stream_id, 5, True) is True
    assert client.submit_priority_update(stream_id, 0, False) is True
    _pump(client, server)

    # サーバーで PriorityUpdate イベントを受信する (stream_id と
    # priority field value を含む)
    priority_events = [
        e
        for e in _drain_events(server)
        if e.type == http2.EventType.PRIORITY_UPDATE
    ]
    assert len(priority_events) == 2
    assert priority_events[0].stream_id == stream_id
    assert priority_events[0].priority_field_value == "u=5, i"
    assert priority_events[1].stream_id == stream_id
    assert priority_events[1].priority_field_value == "u=0"


def test_http2_priority_update_noop_without_no_rfc7540_priorities() -> None:
    """ピアが NO_RFC7540_PRIORITIES を送信しない場合は noop で成功することを確認"""
    client = http2.Connection.create_client(http2.Config())
    server_config = http2.Config()
    server_config.is_server = True
    server_config.no_rfc7540_priorities = False
    server = http2.Connection.create_server(server_config)
    _exchange_settings(client, server)

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    # ピアが NO_RFC7540_PRIORITIES=0 を送信しているため noop (成功) になり、
    # PRIORITY_UPDATE フレームは送出されない
    assert client.submit_priority_update(stream_id, 0, False) is True
    _pump(client, server)
    assert not any(
        e.type == http2.EventType.PRIORITY_UPDATE
        for e in _drain_events(server)
    )


def test_http2_change_extpri_stream_priority() -> None:
    """サーバーがストリームの優先度を変更できることを確認

    ローカルなスケジューリング変更のみでワイヤ上の効果が無いため、
    返り値での確認となる
    """
    client, server = _create_connection_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    # urgency の境界値 (0 と 7) と incremental の両方を確認する
    assert server.change_extpri_stream_priority(stream_id, 0, True) is True
    assert server.change_extpri_stream_priority(stream_id, 7, False) is True
    # 存在しないストリームは False (nghttp2 が INVALID_ARGUMENT を返す)
    assert server.change_extpri_stream_priority(3, 3, False) is False


def test_http2_submit_push_promise() -> None:
    """サーバーが Server Push を宣言できることを確認"""
    client, server = _create_connection_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    # プッシュするリクエストを宣言する
    push_headers = [
        (":method", "GET"),
        (":path", "/pushed"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]
    promised_stream_id = server.submit_push_promise(stream_id, push_headers)
    assert promised_stream_id > 0
    _pump(server, client)

    # クライアントで PushPromise イベントを受信する (promised stream ID と
    # ヘッダーを含む)
    push_events = [
        e for e in _drain_events(client) if e.type == http2.EventType.PUSH_PROMISE
    ]
    assert len(push_events) == 1
    assert push_events[0].stream_id == stream_id
    assert push_events[0].promised_stream_id == promised_stream_id
    for name, value in push_headers:
        assert (name, value) in push_events[0].headers

    # プッシュされたリソースのレスポンスも送信できる (promised stream 上で
    # 通常のレスポンスと同じフロー)
    server.submit_response(promised_stream_id, [(":status", "200")])
    server.send_data(promised_stream_id, b"pushed-body", True)
    _pump(server, client)
    events = _drain_events(client)
    pushed_data = b"".join(
        e.data
        for e in events
        if e.type == http2.EventType.DATA and e.stream_id == promised_stream_id
    )
    assert pushed_data == b"pushed-body"


def test_http2_select_alpn() -> None:
    """ALPN プロトコルが選択できることを確認"""
    # h2 を優先して選択する
    assert http2.select_alpn(["http/1.1", "h2"]) == "h2"
    assert http2.select_alpn(["h2", "http/1.1"]) == "h2"
    assert http2.select_alpn(["h2"]) == "h2"
    assert http2.select_alpn(["http/1.1"]) == "http/1.1"
    # 一致しない場合は None
    assert http2.select_alpn(["spdy/3"]) is None
    # 空リストは None
    assert http2.select_alpn([]) is None
    # RFC 7301 の 1-255 バイトを超えるプロトコル名は無視される
    assert http2.select_alpn(["x" * 300, "h2"]) == "h2"


def test_http2_message_ext_guards() -> None:
    """利用できない側で False / -1 になることを確認"""
    client, server = _create_connection_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    # サーバーセッションでは PRIORITY_UPDATE を送信できない
    assert server.submit_priority_update(stream_id, 0, False) is False
    # クライアントセッションでは優先度変更と Server Push を送信できない
    assert client.change_extpri_stream_priority(stream_id, 0, False) is False
    assert client.submit_push_promise(stream_id, _request_headers()) == -1
    # クライアントセッションではトレーラを送信できない
    assert client.submit_trailer(stream_id, [("x-trailer", "value")]) is False
    # RFC 9218 で定義された urgency の範囲 (0-7) を超える場合は False
    assert client.submit_priority_update(stream_id, 8, False) is False
    assert server.change_extpri_stream_priority(stream_id, 8, False) is False
    # ストリーム ID 0 (コネクション全体) は受け付けない
    assert client.submit_priority_update(0, 0, False) is False
    assert server.change_extpri_stream_priority(0, 0, False) is False
    assert server.submit_trailer(0, [("x-trailer", "value")]) is False
    assert server.submit_push_promise(0, _request_headers()) == -1
    # 負のストリーム ID も受け付けない
    assert client.submit_priority_update(-1, 0, False) is False
    assert server.change_extpri_stream_priority(-1, 0, False) is False
    assert server.submit_trailer(-1, [("x-trailer", "value")]) is False
    assert server.submit_push_promise(-1, _request_headers()) == -1

    # 存在しないストリームには送信できない
    assert server.submit_trailer(999, [("x-trailer", "value")]) is False
    # レスポンス (データプロバイダ) が設定されていないストリームにも
    # トレーラを送信できない
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is False
    # 存在しない親ストリームへの Server Push 宣言は -1
    assert server.submit_push_promise(999, _request_headers()) == -1
    # 存在しないストリームへの PRIORITY_UPDATE は送出される (nghttp2 は
    # stream_id の存在を検証しない。RFC 9218 では受信側の扱いに委ねられる)
    assert client.submit_priority_update(999, 0, False) is True
    _pump(client, server)
    assert any(
        e.type == http2.EventType.PRIORITY_UPDATE and e.stream_id == 999
        for e in _drain_events(server)
    )

    # トレーラセクションは 1 つのみ (RFC 9113 8.1 節) のため、同一ストリーム
    # への再予約はできない
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"body", False)
    assert server.submit_trailer(stream_id, [("x-trailer", "first")]) is True
    assert server.submit_trailer(stream_id, [("x-trailer", "second")]) is False
    _pump(server, client)
    assert any(
        e.type == http2.EventType.HEADERS
        and ("x-trailer", "first") in e.headers
        for e in _drain_events(client)
    )

    # サーバーが GOAWAY を送信するとクライアントが閉じる
    # (閉じたコネクションのガードは test_http2_closed_connection_guards で確認)


def test_http2_closed_connection_guards() -> None:
    """コネクションが閉じている場合に False / -1 になることを確認"""
    # サーバー側が閉じる場合 (クライアントの GOAWAY 受信)
    client, server = _create_connection_pair()
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)
    client.goaway()
    _pump(client, server)
    assert server.is_closed() is True
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is False
    assert server.submit_push_promise(stream_id, _request_headers()) == -1
    assert server.submit_priority_update(stream_id, 0, False) is False
    assert server.change_extpri_stream_priority(stream_id, 0, False) is False

    # クライアント側が閉じる場合 (サーバーの GOAWAY 受信)。closed_ 起因の
    # ガード確認のため、クライアントセッションでは is_server_ ガードに
    # 抵触しない submit_priority_update / change_extpri で検証する
    client2, server2 = _create_connection_pair()
    stream_id2 = client2.submit_request(_request_headers())
    assert stream_id2 > 0
    _pump(client2, server2)
    server2.goaway()
    _pump(server2, client2)
    assert client2.is_closed() is True
    assert client2.submit_priority_update(stream_id2, 0, False) is False
    assert client2.change_extpri_stream_priority(stream_id2, 0, False) is False
