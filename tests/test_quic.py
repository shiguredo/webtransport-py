"""QUIC テスト"""

# Sans-IO テスト用の固定パスアドレス
CLIENT_ADDR = ("127.0.0.1", 50000)
SERVER_ADDR = ("127.0.0.1", 4433)


def test_quic_import():
    """QUIC モジュールがインポートできることを確認"""
    from webtransport import quic

    assert quic is not None


def test_quic_version():
    """ngtcp2 バージョンが取得できることを確認"""
    from webtransport import quic

    version = quic.get_version()
    assert version is not None
    assert isinstance(version, str)
    assert len(version) > 0
    print(f"ngtcp2 version: {version}")


def test_quic_config():
    """QUIC Config が作成できることを確認"""
    from webtransport import quic

    config = quic.Config()
    assert config.max_streams_bidi == 100
    assert config.max_streams_uni == 100
    assert config.max_data == 1048576
    assert config.idle_timeout_ns == 30000000000  # 30秒 (ナノ秒)

    # 設定を変更
    config.max_streams_bidi = 200
    assert config.max_streams_bidi == 200

    # 新しい設定項目のテスト
    assert config.alpn_protocols == []
    assert config.server_name == ""
    assert config.enable_datagram is True


def test_quic_event_type():
    """QUIC EventType が定義されていることを確認"""
    from webtransport import quic

    assert quic.EventType.HANDSHAKE_COMPLETED is not None
    assert quic.EventType.CONNECTION_CLOSED is not None
    assert quic.EventType.STREAM_DATA is not None
    assert quic.EventType.STREAM_OPENED is not None
    assert quic.EventType.STREAM_CLOSED is not None
    assert quic.EventType.STREAM_RESET is not None
    assert quic.EventType.DATAGRAM is not None
    assert quic.EventType.CONNECTION_ID_RETIRED is not None


def test_quic_connection_client():
    """QUIC Connection (クライアント) が作成できることを確認"""
    from webtransport import quic

    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.server_name = "localhost"

    conn = quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)
    assert conn is not None

    # 初期状態の確認
    assert conn.is_closed() is False
    assert conn.is_handshake_completed() is False
    assert conn.is_established() is False

    # 接続 ID の取得
    cid = conn.get_connection_id()
    assert cid is not None
    assert len(cid) >= 8  # 最小の接続 ID 長

    # 接続を閉じる
    conn.close()
    assert conn.is_closed() is True
