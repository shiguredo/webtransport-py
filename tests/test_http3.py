"""HTTP/3 テスト"""


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
    assert http3.EventType.PUSH_PROMISE is not None
    assert http3.EventType.GO_AWAY is not None
    assert http3.EventType.WEBTRANSPORT_SESSION_READY is not None
    assert http3.EventType.WEBTRANSPORT_STREAM_DATA is not None
    assert http3.EventType.WEBTRANSPORT_DATAGRAM is not None


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
