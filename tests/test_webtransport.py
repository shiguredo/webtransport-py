"""WebTransport テスト"""


def test_import():
    """モジュールがインポートできることを確認"""
    import webtransport

    assert webtransport is not None


def test_import_quic():
    """QUIC サブモジュールがインポートできることを確認"""
    import webtransport.quic

    assert webtransport.quic is not None


def test_import_http2():
    """HTTP/2 サブモジュールがインポートできることを確認"""
    import webtransport.http2

    assert webtransport.http2 is not None


def test_import_http3():
    """HTTP/3 サブモジュールがインポートできることを確認"""
    import webtransport.http3

    assert webtransport.http3 is not None


def test_h3_session_import():
    """WebTransport over HTTP/3 モジュールがインポートできることを確認"""
    from webtransport import h3

    assert h3 is not None


def test_h3_session_config():
    """WebTransport H3 Config が作成できることを確認"""
    from webtransport import h3

    config = h3.Config()
    assert config.max_field_section_size == 65536
    assert config.qpack_max_dtable_capacity == 4096
    assert config.is_server is False


def test_h3_session_event_type():
    """WebTransport H3 EventType が定義されていることを確認"""
    from webtransport import h3

    assert h3.EventType.SESSION_READY is not None
    assert h3.EventType.SESSION_CLOSED is not None
    assert h3.EventType.STREAM_OPENED is not None
    assert h3.EventType.STREAM_DATA is not None
    assert h3.EventType.STREAM_CLOSED is not None
    assert h3.EventType.DATAGRAM is not None
    assert h3.EventType.ERROR is not None


def test_h3_session_client():
    """WebTransport H3 Session (クライアント) が作成できることを確認"""
    from webtransport import h3

    config = h3.Config()
    session = h3.Session.create_client(config)
    assert session is not None
    assert session.is_closed() is False

    # 必要なストリームの確認
    required = session.get_required_streams()
    assert required is not None
    assert len(required) == 3  # control, qpack_encoder, qpack_decoder


def test_h3_session_server():
    """WebTransport H3 Session (サーバー) が作成できることを確認"""
    from webtransport import h3

    config = h3.Config()
    config.is_server = True
    session = h3.Session.create_server(config)
    assert session is not None
    assert session.is_closed() is False


def test_h3_session_methods():
    """WebTransport H3 Session のメソッドが呼び出せることを確認"""
    from webtransport import h3

    config = h3.Config()
    session = h3.Session.create_client(config)

    # 空のデータグラムリストを取得
    datagrams = session.get_datagrams_to_send()
    assert datagrams == []

    # 空のストリームリストを取得
    streams = session.get_streams_to_send()
    assert streams == []

    # セッション ID リストを取得 (空)
    session_ids = session.get_session_ids()
    assert session_ids == []

    # イベントがないことを確認
    event = session.next_event()
    assert event is None


# ========== WebTransport over HTTP/2 テスト ==========


def test_h2_session_import():
    """WebTransport over HTTP/2 モジュールがインポートできることを確認"""
    from webtransport import h2

    assert h2 is not None


def test_h2_session_config():
    """WebTransport H2 Config が作成できることを確認"""
    from webtransport import h2

    config = h2.Config()
    assert config.initial_window_size == 65535
    assert config.max_concurrent_streams == 100
    assert config.max_frame_size == 16384
    assert config.is_server is False


def test_h2_session_event_type():
    """WebTransport H2 EventType が定義されていることを確認"""
    from webtransport import h2

    assert h2.EventType.SESSION_READY is not None
    assert h2.EventType.SESSION_CLOSED is not None
    assert h2.EventType.SESSION_DRAINING is not None
    assert h2.EventType.STREAM_DATA is not None
    assert h2.EventType.STREAM_RESET is not None
    assert h2.EventType.STOP_SENDING is not None
    assert h2.EventType.DATAGRAM is not None
    assert h2.EventType.ERROR is not None


def test_h2_session_client():
    """WebTransport H2 Session (クライアント) が作成できることを確認"""
    from webtransport import h2

    config = h2.Config()
    session = h2.Session.create_client(config)
    assert session is not None
    assert session.is_closed() is False
    assert session.want_write() is True  # SETTINGS を送信する必要がある

    # 送信データの取得 (HTTP/2 preface + SETTINGS)
    data = session.send()
    assert data is not None
    assert len(data) > 0


def test_h2_session_server():
    """WebTransport H2 Session (サーバー) が作成できることを確認"""
    from webtransport import h2

    config = h2.Config()
    config.is_server = True
    session = h2.Session.create_server(config)
    assert session is not None
    assert session.is_closed() is False


def test_h2_session_methods():
    """WebTransport H2 Session のメソッドが呼び出せることを確認"""
    from webtransport import h2

    config = h2.Config()
    session = h2.Session.create_client(config)

    # セッション ID リストを取得 (空)
    session_ids = session.get_session_ids()
    assert session_ids == []

    # イベントがないことを確認
    event = session.next_event()
    assert event is None
