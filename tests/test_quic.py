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


def test_quic_config_enable_reset_stream_at():
    """QUIC Config の enable_reset_stream_at が既定 true で設定できることを確認"""
    from webtransport import quic

    config = quic.Config()
    assert config.enable_reset_stream_at is True

    # 設定を変更できる (欠落ピアのテスト用)
    config.enable_reset_stream_at = False
    assert config.enable_reset_stream_at is False


def test_remote_transport_params_after_handshake():
    """ハンドシェイク後の remote transport parameter getter が正しい値を返すことを確認

    draft-ietf-webtrans-http3-16 Section 3.1 の MUST (max_datagram_frame_size
    > 0 と reset_stream_at の送信) に対応する getter の検証。通常ピア同士
    では両側とも満たされ、transport parameter 未受信 (ハンドシェイク前) は
    None を返す。
    """
    from conftest import create_client_server_pair, perform_handshake

    client, server, initial_packet = create_client_server_pair()

    # ハンドシェイク前は None (transport parameter 未受信)
    assert client.remote_max_datagram_frame_size is None
    assert client.remote_reset_stream_at is None

    assert perform_handshake(client, server, initial_packet) is True

    # 通常ピアは両方の要件を満たす
    assert client.remote_max_datagram_frame_size == 65536
    assert client.remote_reset_stream_at is True
    assert server.remote_max_datagram_frame_size == 65536
    assert server.remote_reset_stream_at is True


def test_remote_transport_params_missing_peers():
    """欠落させた transport parameter が remote getter で検出できることを確認

    enable_datagram=False (max_datagram_frame_size 欠落) と
    enable_reset_stream_at=False (reset_stream_at 欠落) を設定したピアを
    用意し、対向の getter が要件未達を検出できることを検証する
    (draft-ietf-webtrans-http3-16 Section 3.1 の MUST 検証の基盤)。
    """
    from conftest import CERTFILE, KEYFILE, perform_handshake

    from webtransport import quic

    client_config = quic.Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"
    client_config.enable_datagram = False
    client_config.enable_reset_stream_at = False

    server_config = quic.Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = quic.Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    initial_packet = client.send()
    assert initial_packet is not None
    server = quic.Connection.accept(
        server_config,
        initial_packet.data,
        SERVER_ADDR,
        CLIENT_ADDR,
    )

    assert perform_handshake(client, server, initial_packet.data) is True

    # 対向 (サーバー) から見た欠落ピア (クライアント) の transport parameter
    # max_datagram_frame_size は 0 (TP 欠落時は値なし) になり、要件未達を
    # 検出できる
    assert server.remote_max_datagram_frame_size == 0
    assert server.remote_reset_stream_at is False
