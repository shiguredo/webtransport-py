"""HTTP/2 テスト"""

from __future__ import annotations

import pytest
from conftest import (
    _create_http2_pair,
    _drain_events,
    _exchange_http2_settings,
    _h2_pump,
)

from webtransport import http2


def test_http2_import():
    """HTTP/2 モジュールがインポートできることを確認"""
    from webtransport import http2

    assert http2 is not None


def test_http2_version():
    """nghttp2 バージョンが取得できることを確認"""
    from webtransport import http2

    version = http2.get_version()
    assert version is not None
    assert isinstance(version, str)
    assert len(version) > 0
    print(f"nghttp2 version: {version}")


def test_http2_config():
    """HTTP/2 Config が作成できることを確認"""
    from webtransport import http2

    config = http2.Config()
    assert config.initial_window_size == 65535
    assert config.max_concurrent_streams == 100
    assert config.max_frame_size == 16384
    assert config.is_server is False

    # 設定を変更
    config.is_server = True
    assert config.is_server is True


def test_http2_event_type():
    """HTTP/2 EventType が定義されていることを確認"""
    from webtransport import http2

    assert http2.EventType.HEADERS is not None
    assert http2.EventType.DATA is not None
    assert http2.EventType.STREAM_END is not None
    assert http2.EventType.STREAM_RESET is not None
    assert http2.EventType.GO_AWAY is not None
    assert http2.EventType.WINDOW_UPDATE is not None
    assert http2.EventType.SETTINGS is not None
    assert http2.EventType.PING is not None


def test_http2_connection_client():
    """HTTP/2 Connection (クライアント) が作成できることを確認"""
    from webtransport import http2

    config = http2.Config()
    conn = http2.Connection.create_client(config)
    assert conn is not None

    # 初期状態の確認
    assert conn.is_closed() is False
    assert conn.want_write() is True  # SETTINGS を送信する必要がある

    # 送信データの取得 (SETTINGS フレームが生成されるはず)
    data = conn.send()
    assert data is not None
    assert len(data) > 0  # HTTP/2 preface + SETTINGS

    # GOAWAY を送信
    conn.goaway()


def test_http2_connection_server():
    """HTTP/2 Connection (サーバー) が作成できることを確認"""
    from webtransport import http2

    config = http2.Config()
    conn = http2.Connection.create_server(config)
    assert conn is not None

    # 初期状態の確認
    assert conn.is_closed() is False
    assert conn.want_write() is True  # SETTINGS を送信する必要がある


def test_http2_connection_client_request():
    """HTTP/2 クライアントがリクエストを送信できることを確認"""
    from webtransport import http2

    config = http2.Config()
    conn = http2.Connection.create_client(config)

    # SETTINGS を送信
    conn.send()

    # リクエストを送信
    headers = [
        (":method", "GET"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]
    stream_id = conn.submit_request(headers)
    assert stream_id > 0

    # リクエストデータを取得
    data = conn.send()
    assert data is not None
    assert len(data) > 0


def test_http2_request_body_reaches_server():
    """submit_request の後に send_data したリクエストボディがサーバーに届くことを確認

    データプロバイダを渡さないと nghttp2 が HEADERS に END_STREAM を付け、
    後続の DATA が送出されない。常時プロバイダを渡したうえで eof=True の
    send_data により DATA フレームがサーバーの DATA イベントとして届く。
    """
    client = http2.Connection.create_client(http2.Config())
    server_config = http2.Config()
    server_config.is_server = True
    server = http2.Connection.create_server(server_config)
    _exchange_http2_settings(client, server)

    headers = [
        (":method", "POST"),
        (":path", "/echo"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]
    stream_id = client.submit_request(headers)
    assert stream_id > 0

    body = b"request-body"
    client.send_data(stream_id, body, eof=True)
    _h2_pump(client, server)

    events = _drain_events(server)
    data_events = [event for event in events if event.type == http2.EventType.DATA]
    assert [event.data for event in data_events] == [body]
    assert any(event.type == http2.EventType.STREAM_END for event in events)


def test_http2_ping_opaque_data_roundtrip():
    """ping の opaque data がピアで観測され、ACK が同一データで観測されることを確認

    RFC 9113 Section 6.7 の PING は 8 バイトの opaque data を持つ。送信側は
    ping(opaque_data) で任意の 8 バイトを載せられ、受信側では ack=False の
    PING イベントとして観測できる。nghttp2 は受信 PING に自動で ACK を返す
    ため、送信側では ack=True の PING イベントとして同じ opaque data が
    観測できる。
    """
    client = http2.Connection.create_client(http2.Config())
    server_config = http2.Config()
    server_config.is_server = True
    server = http2.Connection.create_server(server_config)
    _exchange_http2_settings(client, server)

    opaque_data = bytes(range(8))
    client.ping(opaque_data)
    _h2_pump(client, server)

    # サーバー側では ACK なしの PING として同じ 8 バイトが観測される
    server_events = _drain_events(server)
    ping_events = [event for event in server_events if event.type == http2.EventType.PING]
    assert len(ping_events) == 1
    assert ping_events[0].ack is False
    assert ping_events[0].opaque_data == opaque_data
    assert len(ping_events[0].opaque_data) == 8

    # サーバーからクライアントへ ACK が返り、同じ opaque data が観測される
    _h2_pump(server, client)
    client_events = _drain_events(client)
    ack_events = [
        event for event in client_events if event.type == http2.EventType.PING and event.ack is True
    ]
    assert len(ack_events) == 1
    assert ack_events[0].opaque_data == opaque_data


def test_http2_ping_without_opaque_data_sends_zero_bytes():
    """ping を引数なしで呼ぶとゼロ 8 バイトの opaque data が送られることを確認"""
    client = http2.Connection.create_client(http2.Config())
    server_config = http2.Config()
    server_config.is_server = True
    server = http2.Connection.create_server(server_config)
    _exchange_http2_settings(client, server)

    client.ping()
    _h2_pump(client, server)

    ping_events = [event for event in _drain_events(server) if event.type == http2.EventType.PING]
    assert len(ping_events) == 1
    assert ping_events[0].opaque_data == b"\x00" * 8


def test_http2_ping_invalid_opaque_data_length_raises():
    """8 バイト以外の opaque data を渡すとエラーになることを確認

    PING の opaque data は 8 バイト固定 (RFC 9113 Section 6.7) であり、
    バインディング層で拒否する (C++ の std::runtime_error は RuntimeError)。
    """
    client = http2.Connection.create_client(http2.Config())

    with pytest.raises(RuntimeError, match="exactly 8 bytes"):
        client.ping(b"short")
    with pytest.raises(RuntimeError, match="exactly 8 bytes"):
        client.ping(b"012345678")


def test_http2_window_update_increment_observed():
    """WINDOW_UPDATE イベントが Window Size Increment を保持することを確認

    RFC 9113 Section 6.9 の WINDOW_UPDATE は 1〜2^31-1 の増分値を持つ。
    サーバーの初期受信ウィンドウ (既定 65535) を超えるボディを送ると、
    サーバーは消費したぶんのウィンドウを再開放し、クライアントは増分値
    付きの WINDOW_UPDATE を観測できる。
    """
    client = http2.Connection.create_client(http2.Config())
    server_config = http2.Config()
    server_config.is_server = True
    server = http2.Connection.create_server(server_config)
    _exchange_http2_settings(client, server)

    headers = [
        (":method", "POST"),
        (":path", "/upload"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]
    stream_id = client.submit_request(headers)
    assert stream_id > 0

    # 初期受信ウィンドウ (65535) を超えるボディを送る (終端しない)
    client.send_data(stream_id, b"x" * (128 * 1024))
    for _ in range(40):
        client_data = client.send()
        server_data = server.send()
        if client_data:
            server.receive(client_data)
        if server_data:
            client.receive(server_data)
        if not client_data and not server_data:
            break

    window_events = [
        event for event in _drain_events(client) if event.type == http2.EventType.WINDOW_UPDATE
    ]
    assert window_events, "ウィンドウ再開放の WINDOW_UPDATE が観測されるべき"
    # 増分値は 1 以上 (RFC 9113 Section 6.9)
    assert all(event.window_size_increment >= 1 for event in window_events)
    # ストリーム単位とコネクション単位の両方の再開放が観測できる
    assert any(event.stream_id == stream_id for event in window_events)
    assert any(event.stream_id == 0 for event in window_events)


def test_http2_window_update_event_fields_default_for_other_events():
    """PING / WINDOW_UPDATE 以外のイベントでは新フィールドが既定値になることを確認"""
    client = http2.Connection.create_client(http2.Config())
    server_config = http2.Config()
    server_config.is_server = True
    server = http2.Connection.create_server(server_config)
    _exchange_http2_settings(client, server)

    headers = [
        (":method", "POST"),
        (":path", "/echo"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]
    stream_id = client.submit_request(headers)
    client.send_data(stream_id, b"body", eof=True)
    _h2_pump(client, server)

    for event in _drain_events(server):
        if event.type in (http2.EventType.PING, http2.EventType.WINDOW_UPDATE):
            continue
        assert event.opaque_data == b""
        assert event.ack is False
        assert event.window_size_increment == 0


def test_http2_test_force_close_helper():
    """テスト専用 _test_force_close が is_closed を立てイベントを積まないことを確認

    nghttp2 の mem_recv / mem_send が負値を返した経路 (closed_ = true) を
    Python から人工的に作る。イベントは push しないため next_event() は
    None を返す。
    """
    client, server = _create_http2_pair()
    stream_id = client.submit_request(
        [
            (":method", "GET"),
            (":path", "/"),
            (":scheme", "https"),
            (":authority", "localhost"),
        ]
    )
    assert stream_id > 0
    _h2_pump(client, server)

    # 送信前は閉じていない。既存イベントを空にしてから検証する
    assert client.is_closed() is False
    _drain_events(client)
    assert client.next_event() is None

    client._test_force_close()

    assert client.is_closed() is True
    # 強制クローズはイベントを積まない
    assert client.next_event() is None
    # 通常の送信も行わない
    assert client.send() is None


def test_http2_test_force_close_does_not_affect_peer():
    """_test_force_close がピア側の接続に影響しないことを確認"""
    client, server = _create_http2_pair()
    _h2_pump(client, server)

    client._test_force_close()

    assert client.is_closed() is True
    assert server.is_closed() is False


def test_http2_send_buffer_uses_offset_not_shift():
    """部分送出が残データのシフトではなくオフセットで進むことを確認

    大バッファを max_frame_size 刻みで送出すると、旧実装は部分コピーごとに
    残データを erase でシフトしていた (O(n²))。オフセット方式ではバッファの
    総バイト数が変わらず、先頭のオフセットだけが進む。テスト専用の
    `_test_stream_buffer_count` / `_test_stream_buffer_remaining` /
    `_test_stream_buffer_offset` で白箱観測する。
    """
    client, server = _create_http2_pair()
    stream_id = client.submit_request(
        [
            (":method", "POST"),
            (":path", "/"),
            (":scheme", "https"),
            (":authority", "localhost"),
        ]
    )
    assert stream_id > 0
    _h2_pump(client, server)
    _drain_events(server)

    server.submit_response(stream_id, [(":status", "200")])
    # max_frame_size (16384) を超えるボディを積む
    body = b"x" * (64 * 1024)
    server.send_data(stream_id, body, True)

    # 送出前にバッファへ積まれている
    assert server._test_stream_buffer_count(stream_id) == 1
    assert server._test_stream_buffer_remaining(stream_id) == len(body)
    assert server._test_stream_buffer_offset(stream_id) == 0

    # DATA フレームが送出されるまで send() を繰り返す。send() は 1 回に
    # つき mem_send が返した 1 チャンクだけを返すため、SETTINGS などの
    # 制御フレームだけが返る回がある
    for _ in range(10):
        packet = server.send()
        assert packet is not None
        if server._test_stream_buffer_offset(stream_id) > 0:
            break

    count = server._test_stream_buffer_count(stream_id)
    remaining = server._test_stream_buffer_remaining(stream_id)
    offset = server._test_stream_buffer_offset(stream_id)

    # バッファの総バイト数は減っていない (シフトしていない)
    assert len(body) == remaining + offset
    # オフセットは送出できた範囲だけ進み、未送出の残りが残る
    assert 0 < offset < len(body)
    assert remaining == len(body) - offset
    # 先頭エントリは送出し切るまで残る (エントリ数は増減しない)
    assert count == 1


def test_http2_large_send_data_integrity():
    """オフセット方式でも大バッファの内容が欠落しないことを確認

    フロー制御のウィンドウ更新を返しながら 100 KiB を送出し、受信側で
    バイト列が完全一致することを確認する。
    """
    client, server = _create_http2_pair()
    stream_id = client.submit_request(
        [
            (":method", "POST"),
            (":path", "/"),
            (":scheme", "https"),
            (":authority", "localhost"),
        ]
    )
    assert stream_id > 0
    _h2_pump(client, server)
    _drain_events(server)

    server.submit_response(stream_id, [(":status", "200")])
    body = bytes(range(256)) * 400
    server.send_data(stream_id, body, True)

    received = bytearray()
    for _ in range(200):
        _h2_pump(server, client)
        for event in _drain_events(client):
            if event.type == http2.EventType.DATA:
                received.extend(event.data)
        _h2_pump(client, server)
        if len(received) == len(body):
            break

    assert bytes(received) == body


def test_http2_send_buffer_offset_with_multiple_entries():
    """複数エントリを積んだ場合もエントリ単位でオフセットが進むことを確認"""
    client, server = _create_http2_pair()
    stream_id = client.submit_request(
        [
            (":method", "POST"),
            (":path", "/"),
            (":scheme", "https"),
            (":authority", "localhost"),
        ]
    )
    assert stream_id > 0
    _h2_pump(client, server)
    _drain_events(server)

    server.submit_response(stream_id, [(":status", "200")])
    server.send_data(stream_id, b"a" * (32 * 1024), False)
    server.send_data(stream_id, b"b" * (32 * 1024), True)

    assert server._test_stream_buffer_count(stream_id) == 2
    assert server._test_stream_buffer_remaining(stream_id) == 32768
    assert server._test_stream_buffer_offset(stream_id) == 0

    # 全量を送出する
    received = bytearray()
    for _ in range(200):
        _h2_pump(server, client)
        for event in _drain_events(client):
            if event.type == http2.EventType.DATA:
                received.extend(event.data)
        _h2_pump(client, server)
        if len(received) == 64 * 1024:
            break

    assert bytes(received) == b"a" * (32 * 1024) + b"b" * (32 * 1024)
    # 送出完了後はバッファが空になる
    assert server._test_stream_buffer_count(stream_id) == 0


def test_http2_empty_send_data_with_eof_closes_stream():
    """空データ + eof=True で END_STREAM が送出されることを確認

    送出するバイトが残っていない場合に read_callback を呼ぶと、nghttp2 は
    空のバッファを defer して EOF を立てる機会を失い、ボディ無しの
    レスポンスで END_STREAM が送出されなくなる。空データの時点で応答を
    終端できることを白箱 (バッファが空になる) と受信側イベントで確認する。
    """
    client, server = _create_http2_pair()
    stream_id = client.submit_request(
        [
            (":method", "GET"),
            (":path", "/empty"),
            (":scheme", "https"),
            (":authority", "localhost"),
        ]
    )
    assert stream_id > 0
    _h2_pump(client, server)
    _drain_events(server)

    server.submit_response(stream_id, [(":status", "204")])
    _h2_pump(server, client)
    _drain_events(client)

    server.send_data(stream_id, b"", True)
    assert server._test_stream_buffer_count(stream_id) == 1

    for _ in range(10):
        _h2_pump(server, client)
        if server._test_stream_buffer_count(stream_id) == 0:
            break

    # 送出し切ったのでバッファは空になる
    assert server._test_stream_buffer_count(stream_id) == 0

    # 受信側は END_STREAM を観測し、ストリームが両側で閉じる
    types = [event.type for event in _drain_events(client)]
    assert http2.EventType.STREAM_END in types
    assert server.stream_local_close(stream_id) is True


def test_http2_empty_send_data_without_eof_keeps_pending_data():
    """空データ + eof=False が送信待ちデータを破棄しないことを確認

    空の送信をユーザーが明示的に呼んだ場合でも、既に積まれた送信待ちデータは
    そのまま残り、後続の送出で欠落しないことを確認する。
    """
    client, server = _create_http2_pair()
    stream_id = client.submit_request(
        [
            (":method", "POST"),
            (":path", "/"),
            (":scheme", "https"),
            (":authority", "localhost"),
        ]
    )
    assert stream_id > 0
    _h2_pump(client, server)
    _drain_events(server)

    server.submit_response(stream_id, [(":status", "200")])
    body = b"payload"
    server.send_data(stream_id, body, False)
    # 空データ (eof なし) を挟んでも既存のエントリは残る
    server.send_data(stream_id, b"", False)

    assert server._test_stream_buffer_count(stream_id) == 2
    assert server._test_stream_buffer_remaining(stream_id) == len(body)
    assert server._test_stream_buffer_offset(stream_id) == 0

    received = bytearray()
    for _ in range(50):
        _h2_pump(server, client)
        for event in _drain_events(client):
            if event.type == http2.EventType.DATA:
                received.extend(event.data)
        _h2_pump(client, server)
        if len(received) == len(body):
            break

    assert bytes(received) == body
