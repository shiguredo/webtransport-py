"""HTTP/2 のメッセージング拡張 API テスト"""

from conftest import (
    _create_http2_pair,
    _drain_events,
    _h2_pump,
)

from webtransport import http2


def _request_headers() -> list[tuple[str, str]]:
    """テスト用のリクエストヘッダー"""
    return [
        (":method", "GET"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]


def test_http2_submit_trailer() -> None:
    """サーバーがレスポンスの後にトレーラを送信できることを確認"""
    client, server = _create_http2_pair()

    # クライアントがリクエストを送信する
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    # サーバーがレスポンス + DATA + トレーラを送信する (DATA は eof=False
    # で積み、トレーラ HEADERS が END_STREAM を担う)
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"response-body", False)
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is True
    _h2_pump(server, client)

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
        if e.type == http2.EventType.TRAILERS and ("x-trailer", "value") in e.headers
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
    client, server = _create_http2_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"response-body", False)
    _h2_pump(server, client)

    # DATA 送出後にトレーラを予約しても、deferred 状態の再開により送信される
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is True
    _h2_pump(server, client)

    events = _drain_events(client)
    assert any(
        e.type == http2.EventType.TRAILERS and ("x-trailer", "value") in e.headers for e in events
    )
    assert any(e.type == http2.EventType.STREAM_END and e.stream_id == stream_id for e in events)


def test_http2_submit_trailer_eof_data() -> None:
    """eof=True のデータが積まれている場合はトレーラを送信できないことを確認"""
    client, server = _create_http2_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    # eof=True のデータを積むと END_STREAM 付き DATA になるため、その後に
    # トレーラを送信できない (RFC 9113 8.1 節)
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"response-body", True)
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is False

    # トレーラなしで flush すると END_STREAM 付きで終端する
    _h2_pump(server, client)
    events = _drain_events(client)
    assert not any(
        e.type == http2.EventType.TRAILERS and ("x-trailer", "value") in e.headers for e in events
    )
    assert any(e.type == http2.EventType.STREAM_END and e.stream_id == stream_id for e in events)

    # flush 済み (ローカル側 half-closed) のストリームにも送信できない
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is False


def test_http2_submit_trailer_reset_stream() -> None:
    """ストリームをリセットすると保留中のトレーラが送信されないことを確認"""
    client, server = _create_http2_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"body", False)
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is True

    # トレーラ送信前にストリームをリセットする
    server.reset_stream(stream_id)
    _h2_pump(server, client)

    events = _drain_events(client)
    assert not any(
        e.type == http2.EventType.TRAILERS and ("x-trailer", "value") in e.headers for e in events
    )
    assert any(e.type == http2.EventType.STREAM_RESET for e in events)


def test_http2_submit_trailer_after_eof_data() -> None:
    """トレーラ予約後に eof=True のデータを積んでもトレーラが送信されることを確認

    トレーラ HEADERS が END_STREAM を担うため、予約済みストリームの
    eof=True は無効化される
    """
    client, server = _create_http2_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    server.submit_response(stream_id, [(":status", "200")])
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is True
    # eof=True でもトレーラが END_STREAM を担うため、eof は無効化される
    server.send_data(stream_id, b"response-body", True)
    _h2_pump(server, client)

    events = _drain_events(client)
    assert any(
        e.type == http2.EventType.TRAILERS and ("x-trailer", "value") in e.headers for e in events
    )
    assert any(e.type == http2.EventType.STREAM_END and e.stream_id == stream_id for e in events)


def test_http2_submit_priority_update() -> None:
    """クライアントが PRIORITY_UPDATE を送信できることを確認

    nghttp2-v2 は受信した PRIORITY_UPDATE を内部のスケジューリングへ適用
    するだけでアプリへ通知しないため、送信側の戻り値と、送信後も通常の
    通信が継続できることを検証する。
    """
    client, server = _create_http2_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    # 優先度を更新する (incremental 付き)。連続送信で優先度の再更新
    # (RFC 9218 の再更新) ができることも確認する
    assert client.submit_priority_update(stream_id, 5, True) is True
    assert client.submit_priority_update(stream_id, 0, False) is True
    _h2_pump(client, server)

    # 優先度更新後も通常のレスポンスが流れる
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"body", True)
    _h2_pump(server, client)
    events = _drain_events(client)
    assert any(e.type == http2.EventType.DATA and e.data == b"body" for e in events)


def test_http2_change_extpri_stream_priority() -> None:
    """サーバーがストリームの優先度を変更できることを確認

    ローカルなスケジューリング変更のみでワイヤ上の効果が無いため、
    返り値での確認となる。
    """
    client, server = _create_http2_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    # urgency の境界値 (0 と 7) と incremental の両方を確認する
    assert server.change_extpri_stream_priority(stream_id, 0, True) is True
    assert server.change_extpri_stream_priority(stream_id, 7, False) is True
    # nghttp2-v2 は存在しないストリームに 0 を返して no-op にする
    assert server.change_extpri_stream_priority(3, 3, False) is True


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
    """利用できない側で False になることを確認"""
    client, server = _create_http2_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    # サーバーセッションでは PRIORITY_UPDATE を送信できない
    assert server.submit_priority_update(stream_id, 0, False) is False
    # クライアントセッションでは優先度変更を送信できない
    assert client.change_extpri_stream_priority(stream_id, 0, False) is False
    # クライアントセッションではトレーラを送信できない
    assert client.submit_trailer(stream_id, [("x-trailer", "value")]) is False
    # RFC 9218 で定義された urgency の範囲 (0-7) を超える場合は False
    assert client.submit_priority_update(stream_id, 8, False) is False
    assert server.change_extpri_stream_priority(stream_id, 8, False) is False
    # ストリーム ID 0 (コネクション全体) は受け付けない
    assert client.submit_priority_update(0, 0, False) is False
    assert server.change_extpri_stream_priority(0, 0, False) is False
    assert server.submit_trailer(0, [("x-trailer", "value")]) is False
    # 負のストリーム ID も受け付けない
    assert client.submit_priority_update(-1, 0, False) is False
    assert server.change_extpri_stream_priority(-1, 0, False) is False
    assert server.submit_trailer(-1, [("x-trailer", "value")]) is False
    # 偶数 (サーバーが応答できない) ストリーム ID は受け付けない
    assert server.submit_trailer(2, [("x-trailer", "value")]) is False

    # 存在しないストリームにはトレーラを送信できない
    assert server.submit_trailer(999, [("x-trailer", "value")]) is False
    # レスポンス (データプロバイダ) が設定されていないストリームにも
    # トレーラを送信できない
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is False
    # 存在しないストリームへの PRIORITY_UPDATE / 優先度変更は no-op で成功する
    # (nghttp2-v2 はストリームの存在を検証しない)
    assert client.submit_priority_update(999, 0, False) is True
    assert server.change_extpri_stream_priority(999, 3, False) is True

    # トレーラセクションは 1 つのみ (RFC 9113 8.1 節) のため、同一ストリーム
    # への再送信はできない
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"body", False)
    assert server.submit_trailer(stream_id, [("x-trailer", "first")]) is True
    assert server.submit_trailer(stream_id, [("x-trailer", "second")]) is False
    _h2_pump(server, client)
    assert any(
        e.type == http2.EventType.TRAILERS and ("x-trailer", "first") in e.headers
        for e in _drain_events(client)
    )


def test_http2_goaway_connection_guards() -> None:
    """GOAWAY 受信後は新規ストリームのみ抑止され既存ストリーム操作は継続することを確認

    RFC 9113 Section 6.8 の graceful shutdown: GOAWAY 受信後も接続は閉じず、
    新規ストリームの開始 (submit_request) のみが goaway_received_ で抑止される。
    既存ストリームへの操作 (レスポンス送出等) は closed_ 起因のガードだけがあり、
    GOAWAY では塞がれない。
    """
    # クライアント側が GOAWAY を送信する場合 (サーバーの GOAWAY 受信)
    client, server = _create_http2_pair()
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)
    client.goaway()
    _h2_pump(client, server)
    assert server.is_closed() is False
    # 既存ストリームへの操作 (レスポンス送出) は継続する
    server.submit_response(stream_id, [(":status", "200")])
    # レスポンスの送出 (既存ストリームの処理継続) がワイヤで確認できる
    response = server.send()
    assert response is not None and len(response) > 0

    # クライアント側が GOAWAY を受信する場合 (サーバーの GOAWAY 送信)。
    client2, server2 = _create_http2_pair()
    stream_id2 = client2.submit_request(_request_headers())
    assert stream_id2 > 0
    _h2_pump(client2, server2)
    server2.goaway()
    _h2_pump(server2, client2)
    assert client2.is_closed() is False
    # 新規リクエストは抑止される
    assert client2.submit_request(_request_headers()) == -1
    # 既存ストリームへの操作 (優先度更新) は継続する
    assert client2.submit_priority_update(stream_id2, 0, False) is True


def test_http2_goaway_after_response_delivered() -> None:
    """GOAWAY 受信後も既存ストリームのレスポンスが送出・受信されることを確認

    RFC 9113 Section 6.8 の graceful shutdown: GOAWAY 受信後も既存ストリーム
    の送受信は継続する。サーバーは GOAWAY を受信した後でも、既存のリクエスト
    ストリームへのレスポンス (HEADERS + DATA) を送出し、クライアントが
    HEADERS / DATA イベントとして受信できることを検証する。
    """
    client, server = _create_http2_pair()

    # クライアントがリクエストを送信する
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    # クライアントが GOAWAY を送信し、サーバーが受信する
    # (接続は閉じず graceful shutdown になる)
    client.goaway()
    _h2_pump(client, server)
    assert server.is_closed() is False

    # GOAWAY 受信後に進行中ストリームへのレスポンス送出が継続する
    # (send() は送信バッファを 1 回で全量返すとは限らないため、None まで
    # ループで取り出してピアに渡す)
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"response-body", True)
    for _ in range(64):
        response = server.send()
        if response is None:
            break
        assert client.receive(response) > 0

    # ピア (クライアント) で HEADERS + DATA イベントとして受信される
    events = _drain_events(client)
    assert any(event.type == http2.EventType.HEADERS for event in events)
    assert any(
        event.type == http2.EventType.DATA and event.data == b"response-body" for event in events
    )


def test_http2_informational_response_distinguished() -> None:
    """1xx レスポンスが INFORMATIONAL、最終レスポンスが HEADERS になることを確認

    RFC 9113 Section 8.1 の interim response (1xx) と最終レスポンスを区別して
    観測できる。nghttp2 の nghttp2_headers_category は 1xx を先に送ると
    最初の HEADERS を HCAT_RESPONSE、最終レスポンスを HCAT_HEADERS とするため
    cat では分類できない。`:status` の値で判定する。
    """
    client, server = _create_http2_pair()

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)
    _drain_events(client)

    # 1xx (103 Early Hints) を送ってから最終レスポンスを送る
    server.submit_response(stream_id, [(":status", "103"), ("link", "</style.css>")])
    _h2_pump(server, client)

    events = _drain_events(client)
    assert len(events) == 1
    assert events[0].type == http2.EventType.INFORMATIONAL
    assert events[0].stream_id == stream_id
    assert dict(events[0].headers)[":status"] == "103"

    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"body", False)
    assert server.submit_trailer(stream_id, [("x-trailer", "value")]) is True
    _h2_pump(server, client)

    events = _drain_events(client)
    assert events[0].type == http2.EventType.HEADERS
    assert dict(events[0].headers)[":status"] == "200"
    assert any(
        e.type == http2.EventType.TRAILERS and dict(e.headers) == {"x-trailer": "value"}
        for e in events
    )
    # トレーラは HEADERS としては届かない
    assert not any(
        e.type == http2.EventType.HEADERS and ":status" not in dict(e.headers) for e in events
    )
