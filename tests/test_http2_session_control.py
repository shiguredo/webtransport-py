"""HTTP/2 のセッション制御 API テスト"""

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


def test_http2_terminate_session() -> None:
    """GOAWAY 送信後にセッションが即時終了状態になることを確認"""
    client, server = _create_http2_pair()

    # セッションを即時終了する (GOAWAY が送信キューに積まれる)
    assert client.terminate_session(42, 2) is True
    assert client.is_closed() is False

    # 2 回目の呼び出しは何もせず成功を返す (GOAWAY の送出は 1 枚のまま)
    assert client.terminate_session(42, 2) is True

    # GOAWAY 送出前は送信待ちがある
    assert client.want_write() is True

    # GOAWAY を送出すると送信待ちが無くなる
    # (goaway() と異なり GOAWAY 送信後に want_read / want_write が 0 になる
    # ことが保証される)
    _h2_pump(client, server)
    assert client.want_write() is False
    assert client.is_closed() is False

    # ピア側で GOAWAY を受信しても graceful shutdown (RFC 9113 6.8) のため
    # 接続は閉じず、GO_AWAY イベント (error_code と last_stream_id) が届く
    assert server.is_closed() is False
    goaway_events = [
        event for event in _drain_events(server) if event.type == http2.EventType.GO_AWAY
    ]
    assert len(goaway_events) == 1
    assert goaway_events[0].error_code == 42
    # nghttp2-v2 の nghttp2_conn_terminate は last_stream_id にライブラリが
    # 処理済みの値 (この時点では 0) を使うため、指定値 2 は反映されない
    assert goaway_events[0].last_stream_id == 0


def test_http2_terminate_session_after_goaway() -> None:
    """goaway() の後に terminate_session() を呼んでも GOAWAY が重複しないことを確認

    nghttp2-v2 の GOAWAY は接続に 1 つだけ予約される (nghttp2_conn_shutdown
    のあとに nghttp2_conn_terminate を呼ぶと、同じ GOAWAY が即時終了の
    指定で上書きされる)。v1 のように 2 枚は送出されない。
    """
    client, server = _create_http2_pair()

    client.goaway()
    assert client.terminate_session(0, 0) is True

    # 送信キューには GOAWAY が 1 枚だけ積まれる
    first = client.send()
    assert first is not None
    assert client.want_write() is False
    assert client.send() is None

    # 受信側は GOAWAY を 1 件受信し、接続は閉じない
    server.receive(first)
    assert server.is_closed() is False
    goaway_events = [
        event for event in _drain_events(server) if event.type == http2.EventType.GO_AWAY
    ]
    assert len(goaway_events) == 1
    assert goaway_events[0].error_code == 0


def test_http2_terminate_session_last_stream_id_parity() -> None:
    """パリティ違反の last_stream_id で False になりセッションが壊れないことを確認

    パリティ違反を nghttp2 に渡すと受信処理が無視状態になってしまう
    ため、C++ 側のガードで呼び出し前に False を返す
    """
    client, server = _create_http2_pair()

    # クライアントセッションで奇数 (自分が開始したストリーム ID) は False
    assert client.terminate_session(0, 1) is False
    # サーバーセッションで偶数 (自分が開始したストリーム ID) は False
    assert server.terminate_session(0, 2) is False
    # 負の last_stream_id も False
    assert client.terminate_session(0, -2) is False
    assert server.terminate_session(0, -4) is False

    # パリティ違反の後も通信が継続できる (受信処理が壊れない)
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)
    assert any(event.type == http2.EventType.HEADERS for event in _drain_events(server))

    # 正しいパリティなら成功する (クライアントは偶数 / サーバーは奇数)
    assert client.terminate_session(0, 2) is True
    assert server.terminate_session(0, 1) is True


def test_http2_set_local_window_size_increase() -> None:
    """ローカルウィンドウの増加が受信量として反映されることを確認

    nghttp2-v2 は WINDOW_UPDATE の受信や送信をアプリへ通知しないため、
    ウィンドウ残量の getter と、初期ウィンドウ (65535) を超えるデータが
    実際に受信できることで検証する
    """
    client, server = _create_http2_pair()

    # コネクションのローカルウィンドウを増加させる
    assert client.set_local_window_size(0, 131072) is True

    # ローカル側のウィンドウサイズも絶対値で設定される
    assert client.local_window_size == 131072

    _h2_pump(client, server)

    # ストリームのローカルウィンドウも増加させる
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    assert client.set_local_window_size(stream_id, 131072) is True
    assert client.stream_local_window_size(stream_id) == 131072
    _h2_pump(client, server)

    # 初期ウィンドウ (65535) を超える 120000 バイトが受信できる
    # (コネクションとストリームの両方を増やした効果をワイヤで確認する)
    body = b"x" * 120000
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, body, True)
    received = bytearray()
    for _ in range(400):
        _h2_pump(server, client)
        for event in _drain_events(client):
            if event.type == http2.EventType.DATA:
                received.extend(event.data)
        _h2_pump(client, server)
        if len(received) == len(body):
            break
    assert bytes(received) == body


def test_http2_set_local_window_size_decrease() -> None:
    """ローカルウィンドウの減少がピアへ通知されず受信絞り込みになることを確認

    nghttp2 は減少時に local_window_size と recv_window_size を同時に
    減らすため、getter の値 (local_window_size - recv_window_size) は
    変わらない。効果は「WINDOW_UPDATE を送らずに受信できる量」が減る
    こととして現れる。コネクションとストリームの両方を減らす (片方だけ
    減らすと、減らしていない側の WINDOW_UPDATE が送出されるため)
    """
    client, server = _create_http2_pair()

    # コネクションのローカルウィンドウを 32768 に減少させる
    assert client.set_local_window_size(0, 32768) is True

    # 減少はピアに通知されないため getter の値は変わらない
    assert client.local_window_size == 65535

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)

    # ストリームのローカルウィンドウも 32768 に減少させる
    assert client.set_local_window_size(stream_id, 32768) is True

    # ピアが 32767 バイト送信しても WINDOW_UPDATE は送出されない
    # (通常のウィンドウサイズなら 32767 バイトの受信で送出される)
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"0" * 32767, False)
    _h2_pump(server, client)

    # 受信側から WINDOW_UPDATE は送出されない。nghttp2-v2 は WINDOW_UPDATE
    # をイベント化しないため、送信待ちが無いことで確認する
    assert client.want_write() is False


def test_http2_session_control_guards() -> None:
    """ガード経路で False になることを確認"""
    client, server = _create_http2_pair()

    # 負の window_size は False
    assert client.set_local_window_size(0, -1) is False

    # 存在しないストリームは成功扱い (True)
    assert client.set_local_window_size(3, 65535) is True
    # 負の stream_id も存在しないストリーム扱いで成功になる
    # (nghttp2 v1.70.0 の実装。ヘッダー doc の INVALID_ARGUMENT とは異なる)
    assert client.set_local_window_size(-1, 65535) is True

    # GOAWAY 受信後のガード確認: graceful shutdown では接続は閉じないため、
    # セッション制御 API は継続する (RFC 9113 6.8)
    client.goaway()
    _h2_pump(client, server)
    assert server.is_closed() is False
    assert server.set_local_window_size(0, 65535) is True
    assert server.terminate_session(0, 0) is True
