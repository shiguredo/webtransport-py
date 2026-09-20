"""HTTP/3 テスト"""

from __future__ import annotations

from conftest import _encode_varint

from webtransport import http3
from webtransport.http3.constants import H3_NO_ERROR


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


def _inject_partial_headers(conn: http3.Connection, stream_id: int) -> int:
    """ヘッダーブロックの受信途中の状態を作る

    HEADERS フレーム (Type 0x01) の Length を実際に渡すペイロードより大きく
    宣言する。宣言した Length に達していないため end_headers_cb は発火せず、
    begin_headers_cb が作った Http3Connection::pending_headers_ のエントリが
    残る。戻り値は receive_stream_data の戻り値で、0 の場合はエントリが
    作られていない (接続が閉じている等)。
    """
    frame = bytes([0x01]) + _encode_varint(100) + b"\x00\x00"
    return conn.receive_stream_data(stream_id, frame, False)


def test_http3_import() -> None:
    """HTTP/3 モジュールがインポートできることを確認"""
    from webtransport import http3

    assert http3 is not None


def test_http3_version() -> None:
    """nghttp3 バージョンが取得できることを確認"""
    from webtransport import http3

    version = http3.get_version()
    assert version is not None
    assert isinstance(version, str)
    assert len(version) > 0
    print(f"nghttp3 version: {version}")


def test_http3_config() -> None:
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


def test_http3_event_type() -> None:
    """HTTP/3 EventType が定義されていることを確認"""
    from webtransport import http3

    assert http3.EventType.HEADERS is not None
    assert http3.EventType.DATA is not None
    assert http3.EventType.STREAM_END is not None
    assert http3.EventType.GO_AWAY is not None


def test_http3_connection_client() -> None:
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


def test_http3_connection_server() -> None:
    """HTTP/3 Connection (サーバー) が作成できることを確認"""
    from webtransport import http3

    config = http3.Config()
    config.is_server = True
    conn = http3.Connection.create_server(config)
    assert conn is not None

    # 初期状態の確認
    assert conn.is_closed() is False


def test_http3_connection_with_webtransport() -> None:
    """HTTP/3 Connection (WebTransport 有効) が作成できることを確認"""
    from webtransport import http3

    config = http3.Config()
    config.enable_webtransport = True
    config.enable_h3_datagram = True
    conn = http3.Connection.create_client(config)
    assert conn is not None
    assert conn.is_closed() is False


def test_http3_test_force_close_helper() -> None:
    """テスト専用 _test_force_close が is_closed を立てイベントを積まないことを確認

    nghttp3 の read_stream2 / writev_stream が負値を返した経路 (closed_) を
    Python から人工的に作る。イベントは push しないため next_event() は
    None を返す。
    """
    client, _server = _create_connection_pair()

    # 既存イベントを空にしてから検証する
    assert client.is_closed() is False
    while client.next_event() is not None:
        pass

    client._test_force_close()

    assert client.is_closed() is True
    # 強制クローズはイベントを積まない
    assert client.next_event() is None


def test_http3_test_force_close_does_not_affect_peer() -> None:
    """_test_force_close がピア側の接続に影響しないことを確認"""
    client, server = _create_connection_pair()

    client._test_force_close()

    assert client.is_closed() is True
    assert server.is_closed() is False


def test_http3_protocol_error_event_reports_wire_code() -> None:
    """不正フレームで Error イベントが H3 ワイヤーコードとメッセージを載せることを確認

    RFC 9114 Section 4.1 は HEADERS より前の DATA フレームを
    H3_FRAME_UNEXPECTED とする。nghttp3 の負値 return から
    nghttp3_error_to_h3_wire_code が 0x0105 を導出し、Error イベントとして
    アプリに通知される。
    """
    from webtransport.http3.constants import H3_FRAME_UNEXPECTED

    _client, server = _create_connection_pair()
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


def test_http3_non_error_events_have_no_error_message() -> None:
    """Error 以外のイベントでは error_message が空であることを確認"""
    _client, server = _create_connection_pair()

    while True:
        event = server.next_event()
        if event is None:
            break
        assert event.type != http3.EventType.ERROR
        assert event.error_message == ""


def test_http3_reset_stream_releases_pending_headers() -> None:
    """reset_stream が受信途中のヘッダーブロックを解放することを確認

    ヘッダーブロックの受信途中でアプリがストリームをリセットすると、
    nghttp3_conn_shutdown_stream_read は stream_close コールバックを呼ばない
    ため、明示的に解放しないとエントリが接続終了まで残る。アプリがピアの
    RESET_STREAM を受けて Client.reset_stream / Server.reset_stream を呼ぶと
    この経路に到達する (高レベル層は受信したリセットを nghttp3 へ自動転送
    しないため、転送されない限りエントリは残る)。
    """
    _client, server = _create_connection_pair()

    assert _inject_partial_headers(server, 0) > 0
    assert server._has_pending_headers(0) is True, (
        "受信途中のヘッダーブロックのエントリが作られていない"
    )

    server.reset_stream(0, H3_NO_ERROR)
    assert server._has_pending_headers(0) is None, (
        "reset_stream 後に受信途中のヘッダーブロックのエントリが残っている"
    )


def test_http3_close_stream_releases_pending_headers() -> None:
    """close_stream が受信途中のヘッダーブロックを解放することを確認

    close_stream は nghttp3_conn_close_stream 経由で stream_close_cb を呼ぶ。
    この経路でエントリが解放されることを確認する。
    """
    _client, server = _create_connection_pair()

    assert _inject_partial_headers(server, 0) > 0
    assert server._has_pending_headers(0) is True, (
        "受信途中のヘッダーブロックのエントリが作られていない"
    )

    server.close_stream(0, H3_NO_ERROR)
    assert server._has_pending_headers(0) is None, (
        "close_stream 後に受信途中のヘッダーブロックのエントリが残っている"
    )


def test_http3_pending_headers_are_independent_per_stream() -> None:
    """受信途中のヘッダーブロックのエントリがストリームごとに独立していることを確認

    片方のストリームの終了が、他方の受信途中のエントリを消さないことを確認する。
    """
    _client, server = _create_connection_pair()

    assert _inject_partial_headers(server, 0) > 0
    assert _inject_partial_headers(server, 4) > 0
    assert server._has_pending_headers(0) is True, (
        "受信途中のヘッダーブロックのエントリが作られていない"
    )
    assert server._has_pending_headers(4) is True, (
        "受信途中のヘッダーブロックのエントリが作られていない"
    )

    server.reset_stream(0, H3_NO_ERROR)
    assert server._has_pending_headers(0) is None, (
        "reset_stream 後に受信途中のヘッダーブロックのエントリが残っている"
    )
    assert server._has_pending_headers(4) is True, (
        "他ストリームのリセットで無関係なエントリまで解放されている"
    )

    server.close_stream(4, H3_NO_ERROR)
    assert server._has_pending_headers(4) is None, (
        "close_stream 後に受信途中のヘッダーブロックのエントリが残っている"
    )
