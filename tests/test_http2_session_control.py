"""HTTP/2 のセッション制御 API テスト"""

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


def test_http2_terminate_session() -> None:
    """GOAWAY 送信後にセッションが即時終了状態になることを確認"""
    client, server = _create_connection_pair()

    # セッションを即時終了する (GOAWAY が送信キューに積まれる)
    assert client.terminate_session(42, 2) is True
    assert client.is_closed() is False

    # 2 回目の呼び出しは何もせず成功を返す (GOAWAY の送出は 1 枚のまま)
    assert client.terminate_session(42, 2) is True

    # GOAWAY 送出前は送信待ちがある
    assert client.want_write() is True

    # GOAWAY を送出するとセッションが終了状態になり送信待ちが無くなる
    # (goaway() と異なり GOAWAY 送信後に want_read / want_write が 0 になる
    # ことが保証される)
    _pump(client, server)
    assert client.want_write() is False
    assert client.is_closed() is False

    # ピア側で GOAWAY を受信して閉鎖状態になる (error_code と
    # last_stream_id も確認する)
    assert server.is_closed() is True
    goaway_events = [
        event for event in _drain_events(server) if event.type == http2.EventType.GO_AWAY
    ]
    assert len(goaway_events) == 1
    assert goaway_events[0].error_code == 42
    assert goaway_events[0].last_stream_id == 2


def test_http2_terminate_session_after_goaway() -> None:
    """goaway() の後に terminate_session() を呼ぶと GOAWAY が 2 枚送出されることを確認

    goaway() は graceful shutdown、terminate_session() は即時終了であり、
    両者は独立した操作 (RFC 9113 6.8 では複数 GOAWAY が許容される)。
    goaway_sent_ は goaway() 専用のため terminate_session() には影響しない。
    受信側は 1 枚目の GOAWAY で閉鎖状態になり 2 枚目を処理しない
    """
    client, server = _create_connection_pair()

    client.goaway()
    assert client.terminate_session(0, 0) is True

    # 送信キューには GOAWAY が 2 枚積まれ、1 回の send() で 1 枚ずつ送出される
    first = client.send()
    assert first is not None
    assert client.want_write() is True
    second = client.send()
    assert second is not None
    assert client.want_write() is False

    # 受信側は 1 枚目で閉鎖状態になり、2 枚目は処理されない
    server.receive(first)
    assert server.is_closed() is True
    assert server.receive(second) == 0
    assert any(event.type == http2.EventType.GO_AWAY for event in _drain_events(server))


def test_http2_terminate_session_last_stream_id_parity() -> None:
    """パリティ違反の last_stream_id で False になりセッションが壊れないことを確認

    パリティ違反を nghttp2 に渡すと受信処理が無視状態になってしまう
    ため、C++ 側のガードで呼び出し前に False を返す
    """
    client, server = _create_connection_pair()

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
    _pump(client, server)
    assert any(event.type == http2.EventType.HEADERS for event in _drain_events(server))

    # 正しいパリティなら成功する (クライアントは偶数 / サーバーは奇数)
    assert client.terminate_session(0, 2) is True
    assert server.terminate_session(0, 1) is True


def test_http2_set_local_window_size_increase() -> None:
    """ローカルウィンドウの増加が WINDOW_UPDATE でピアへ通知されることを確認"""
    client, server = _create_connection_pair()

    # コネクションのローカルウィンドウを増加させる
    assert client.set_local_window_size(0, 131072) is True

    # ローカル側のウィンドウサイズも絶対値で設定される
    assert client.local_window_size == 131072

    # WINDOW_UPDATE が送出され、ピアのリモートウィンドウ残量が増える
    _pump(client, server)
    assert server.remote_window_size == 131072
    assert any(
        event.type == http2.EventType.WINDOW_UPDATE and event.stream_id == 0
        for event in _drain_events(server)
    )

    # ストリームのローカルウィンドウも増加させるとストリーム単位で通知される
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    assert client.set_local_window_size(stream_id, 131072) is True
    _pump(client, server)
    assert server.stream_remote_window_size(stream_id) == 131072
    assert any(
        event.type == http2.EventType.WINDOW_UPDATE and event.stream_id == stream_id
        for event in _drain_events(server)
    )


def test_http2_set_local_window_size_decrease() -> None:
    """ローカルウィンドウの減少がピアへ通知されず受信絞り込みになることを確認

    nghttp2 は減少時に local_window_size と recv_window_size を同時に
    減らすため、getter の値 (local_window_size - recv_window_size) は
    変わらない。効果は「WINDOW_UPDATE を送らずに受信できる量」が減る
    こととして現れる。コネクションとストリームの両方を減らす (片方だけ
    減らすと、減らしていない側の WINDOW_UPDATE が送出されるため)
    """
    client, server = _create_connection_pair()

    # コネクションのローカルウィンドウを 32768 に減少させる
    assert client.set_local_window_size(0, 32768) is True

    # 減少はピアに通知されないため getter の値は変わらない
    assert client.local_window_size == 65535

    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _pump(client, server)

    # ストリームのローカルウィンドウも 32768 に減少させる
    assert client.set_local_window_size(stream_id, 32768) is True

    # ピアが 32767 バイト送信しても WINDOW_UPDATE は送出されない
    # (通常のウィンドウサイズなら 32767 バイトの受信で送出される)
    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"0" * 32767, False)
    _pump(server, client)

    assert client.want_write() is False
    assert not any(event.type == http2.EventType.WINDOW_UPDATE for event in _drain_events(server))


def test_http2_session_control_guards() -> None:
    """ガード経路で False になることを確認"""
    client, server = _create_connection_pair()

    # 負の window_size は False
    assert client.set_local_window_size(0, -1) is False

    # 存在しないストリームは成功扱い (True)
    assert client.set_local_window_size(3, 65535) is True
    # 負の stream_id も存在しないストリーム扱いで成功になる
    # (nghttp2 v1.70.0 の実装。ヘッダー doc の INVALID_ARGUMENT とは異なる)
    assert client.set_local_window_size(-1, 65535) is True

    # コネクションが閉じている場合は False
    client.goaway()
    _pump(client, server)
    assert server.is_closed() is True
    assert server.set_local_window_size(0, 65535) is False
    assert server.terminate_session(0, 0) is False
