"""HTTP/2 テスト"""


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
