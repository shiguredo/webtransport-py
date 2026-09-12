"""HTTP/2 テスト"""

from __future__ import annotations

import pytest
from conftest import (
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
