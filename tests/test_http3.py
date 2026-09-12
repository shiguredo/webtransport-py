"""HTTP/3 テスト"""

from __future__ import annotations

from webtransport import http3


def _pump(src: http3.Connection, dst: http3.Connection) -> None:
    """src の送信データを全て dst に渡す

    QUIC レイヤーを介さず、get_streams_to_send で取り出したデータを
    receive_stream_data で直接渡す (モックなし)
    """
    for _ in range(64):
        sent = False
        for stream_id, data, fin in src.get_streams_to_send():
            dst.receive_stream_data(stream_id, data, fin)
            sent = True
        if not sent:
            break


def test_http3_import():
    """HTTP/3 モジュールがインポートできることを確認"""
    from webtransport import http3

    assert http3 is not None


def test_http3_version():
    """nghttp3 バージョンが取得できることを確認"""
    from webtransport import http3

    version = http3.get_version()
    assert version is not None
    assert isinstance(version, str)
    assert len(version) > 0
    print(f"nghttp3 version: {version}")


def test_http3_config():
    """HTTP/3 Config が作成できることを確認"""
    from webtransport import http3

    config = http3.Config()
    assert config.max_field_section_size == 65536
    assert config.qpack_max_dtable_capacity == 4096
    assert config.enable_webtransport is False
    assert config.enable_h3_datagram is False

    # 設定を変更
    config.enable_webtransport = True
    assert config.enable_webtransport is True


def test_http3_event_type():
    """HTTP/3 EventType が定義されていることを確認"""
    from webtransport import http3

    assert http3.EventType.HEADERS is not None
    assert http3.EventType.DATA is not None
    assert http3.EventType.STREAM_END is not None
    assert http3.EventType.GO_AWAY is not None


def test_http3_connection_client():
    """HTTP/3 Connection (クライアント) が作成できることを確認"""
    from webtransport import http3

    config = http3.Config()
    conn = http3.Connection.create_client(config)
    assert conn is not None

    # 初期状態の確認
    assert conn.is_closed() is False

    # 必要なストリームの確認
    required = conn.get_required_streams()
    assert required is not None
    # HTTP/3 は control, qpack encoder, qpack decoder ストリームが必要


def test_http3_connection_server():
    """HTTP/3 Connection (サーバー) が作成できることを確認"""
    from webtransport import http3

    config = http3.Config()
    config.is_server = True
    conn = http3.Connection.create_server(config)
    assert conn is not None

    # 初期状態の確認
    assert conn.is_closed() is False


def test_http3_connection_with_webtransport():
    """HTTP/3 Connection (WebTransport 有効) が作成できることを確認"""
    from webtransport import http3

    config = http3.Config()
    config.enable_webtransport = True
    config.enable_h3_datagram = True
    conn = http3.Connection.create_client(config)
    assert conn is not None
    assert conn.is_closed() is False


def test_http3_test_force_close_helper():
    """テスト専用 _test_force_close が is_closed を立てイベントを積まないことを確認

    nghttp3 の read_stream2 / writev_stream が負値を返した経路 (closed_) を
    Python から人工的に作る。イベントは push しないため next_event() は
    None を返す。
    """
    client = http3.Connection.create_client(http3.Config())
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)
    _pump(server, client)
    _pump(client, server)

    # 既存イベントを空にしてから検証する
    assert client.is_closed() is False
    while client.next_event() is not None:
        pass

    client._test_force_close()

    assert client.is_closed() is True
    # 強制クローズはイベントを積まない
    assert client.next_event() is None


def test_http3_test_force_close_does_not_affect_peer():
    """_test_force_close がピア側の接続に影響しないことを確認"""
    client = http3.Connection.create_client(http3.Config())
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)
    _pump(server, client)
    _pump(client, server)

    client._test_force_close()

    assert client.is_closed() is True
    assert server.is_closed() is False


def test_http3_protocol_error_event_reports_wire_code():
    """不正フレームで Error イベントが H3 ワイヤーコードとメッセージを載せることを確認

    RFC 9114 Section 4.1 は HEADERS より前の DATA フレームを
    H3_FRAME_UNEXPECTED とする。nghttp3 の負値 return から
    nghttp3_error_to_h3_wire_code が 0x0105 を導出し、Error イベントとして
    アプリに通知される。
    """
    from webtransport.http3.constants import H3_FRAME_UNEXPECTED

    client = http3.Connection.create_client(http3.Config())
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)
    _pump(server, client)
    _pump(client, server)
    while server.next_event() is not None:
        pass

    # HEADERS より前に DATA フレーム (type 0x00) を送る
    frame = bytes([0x00, 0x02]) + b"hi"
    assert server.receive_stream_data(0, frame, False) == 0

    events = []
    while True:
        event = server.next_event()
        if event is None:
            break
        events.append(event)

    error_events = [e for e in events if e.type == http3.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == H3_FRAME_UNEXPECTED
    assert error_events[0].error_code == 0x0105
    assert "FRAME_UNEXPECTED" in error_events[0].error_message
    # 低レベルは自主クローズする
    assert server.is_closed() is True


def test_http3_non_error_events_have_no_error_message():
    """Error 以外のイベントでは error_message が空であることを確認"""
    client = http3.Connection.create_client(http3.Config())
    server_config = http3.Config()
    server_config.is_server = True
    server = http3.Connection.create_server(server_config)
    _pump(server, client)
    _pump(client, server)

    while True:
        event = server.next_event()
        if event is None:
            break
        assert event.type != http3.EventType.ERROR
        assert event.error_message == ""
