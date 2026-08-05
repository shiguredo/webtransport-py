"""QUIC 接続状態・エラー・ピア情報 API のテスト

ngtcp2_conn のコネクションエラー / TLS エラー / トランスポートパラメータ /
バージョン / CLOSING / DRAINING 状態 / 接続 ID 取得 API の動作を確認する。
"""

import datetime
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from webtransport.quic import Config, Connection

# Sans-IO テスト用の固定パスアドレス
CLIENT_ADDR = ("127.0.0.1", 50000)
SERVER_ADDR = ("127.0.0.1", 4433)

# デフォルト設定の値 (quic.h の QuicConfig の初期値)
DEFAULT_IDLE_TIMEOUT_NS = 30_000_000_000
DEFAULT_MAX_DATA = 1_048_576

# ネゴシエーションされる QUIC バージョン (NGTCP2_PROTO_VER_V1)
QUIC_VERSION_V1 = 1


def create_test_certificates():
    """テスト用の自己署名証明書を生成"""
    tmpdir_path = Path(tempfile.mkdtemp())
    certfile = tmpdir_path / "cert.pem"
    keyfile = tmpdir_path / "key.pem"

    private_key = ec.generate_private_key(ec.SECP256R1())

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=1))
        .sign(private_key, hashes.SHA256())
    )

    keyfile.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return str(certfile), str(keyfile)


CERTFILE, KEYFILE = create_test_certificates()


def create_client_server_pair():
    """クライアントとサーバーのペアを作成"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)

    initial_packet = client.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)

    return client, server, initial_packet.data


def perform_handshake(client: Connection, server: Connection, initial_packet: bytes):
    """ハンドシェイクを完了させる"""
    server.receive(initial_packet, SERVER_ADDR, CLIENT_ADDR)

    for _ in range(20):
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)

        if client.is_handshake_completed() and server.is_handshake_completed():
            return True

        if not server_packet and not client_packet:
            break

    return False


def test_conn_state_before_handshake():
    """ハンドシェイク前は初期値が返り、ccerr とリモートパラメータのみ None になる"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.server_name = "localhost"
    client_config.verify_peer = False

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)

    # コネクションエラーは未受信のため None (ccerr は error_code 0 で None)
    assert client.error_code is None
    assert client.reason is None

    # TLS エラー / アラートはエラーが無ければ 0
    assert client.tls_error == 0
    assert client.tls_alert == 0

    # リモートのトランスポートパラメータは未受信のため None
    assert client.remote_max_idle_timeout is None
    assert client.remote_max_udp_payload_size is None
    assert client.remote_initial_max_data is None
    assert client.remote_initial_max_stream_data_bidi_local is None
    assert client.remote_initial_max_stream_data_bidi_remote is None
    assert client.remote_initial_max_stream_data_uni is None
    assert client.remote_initial_max_streams_bidi is None
    assert client.remote_initial_max_streams_uni is None
    assert client.remote_max_datagram_frame_size is None

    # ローカルのトランスポートパラメータは常に取得できる
    assert client.local_max_idle_timeout == DEFAULT_IDLE_TIMEOUT_NS
    assert client.local_max_udp_payload_size > 0
    assert client.local_initial_max_data == DEFAULT_MAX_DATA
    assert client.local_initial_max_stream_data_bidi_local > 0
    assert client.local_initial_max_stream_data_bidi_remote > 0
    assert client.local_initial_max_stream_data_uni > 0
    assert client.local_initial_max_streams_bidi > 0
    assert client.local_initial_max_streams_uni > 0
    assert client.local_max_datagram_frame_size > 0

    # バージョンはネゴシエーション前は 0、クライアント選択は V1
    assert client.negotiated_version == 0
    assert client.client_chosen_version == QUIC_VERSION_V1

    # CLOSING / DRAINING 状態は false
    assert client.in_closing_period is False
    assert client.in_draining_period is False

    # SCID は初期 SCID が 1 個以上、アクティブ DCID はハンドシェイク前は空
    assert len(client.scid) >= 1
    assert client.active_dcid == []


def test_conn_state_after_handshake():
    """ハンドシェイク後はエラー・リモートパラメータ・バージョン・接続 ID が取得できる"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 正常終了時はコネクションエラーは無い (None)
    assert client.error_code is None
    assert client.reason is None
    assert server.error_code is None
    assert server.reason is None

    # 正常確立時は TLS エラーは残らない
    assert client.tls_error == 0
    assert client.tls_alert == 0
    assert server.tls_error == 0
    assert server.tls_alert == 0

    # リモートのトランスポートパラメータが取得できる (ピアの設定値が反映される)
    assert client.remote_max_idle_timeout == DEFAULT_IDLE_TIMEOUT_NS
    assert client.remote_max_udp_payload_size > 0
    assert client.remote_initial_max_data == DEFAULT_MAX_DATA
    assert client.remote_initial_max_stream_data_bidi_local > 0
    assert client.remote_initial_max_stream_data_bidi_remote > 0
    assert client.remote_initial_max_stream_data_uni > 0
    assert client.remote_initial_max_streams_bidi > 0
    assert client.remote_initial_max_streams_uni > 0
    assert client.remote_max_datagram_frame_size > 0

    # サーバー側のリモートパラメータも取得できる (クライアントの設定値が反映される)
    assert server.remote_max_idle_timeout == DEFAULT_IDLE_TIMEOUT_NS
    assert server.remote_max_udp_payload_size > 0
    assert server.remote_initial_max_data == DEFAULT_MAX_DATA
    assert server.remote_initial_max_streams_bidi > 0

    # ローカルのトランスポートパラメータも引き続き取得できる
    assert server.local_max_idle_timeout == DEFAULT_IDLE_TIMEOUT_NS
    assert server.local_initial_max_data == DEFAULT_MAX_DATA

    # ネゴシエーションされたバージョンは V1 になる
    assert client.negotiated_version == QUIC_VERSION_V1
    assert client.client_chosen_version == QUIC_VERSION_V1
    assert server.negotiated_version == QUIC_VERSION_V1

    # SCID / アクティブ DCID が取得できる
    assert len(client.scid) >= 1
    assert len(server.scid) >= 1
    assert len(client.active_dcid) >= 1
    assert len(server.active_dcid) >= 1

    # CLOSING / DRAINING 状態は確立中は false
    assert client.in_closing_period is False
    assert client.in_draining_period is False


def test_conn_state_after_close():
    """接続を閉じた後も状態・エラー・ピア情報が取得できる"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 非ゼロのエラーコードで接続を閉じる
    client.close(0x100, "client error")
    assert client.is_closed()

    # 閉じた後も値が取得できる (ハンドシェイク後に確定した値は保持される)
    assert client.remote_initial_max_data == DEFAULT_MAX_DATA
    assert client.negotiated_version == QUIC_VERSION_V1
    assert len(client.scid) >= 1
    assert len(client.active_dcid) >= 1

    # close() 後は CLOSING 状態になる
    assert client.in_closing_period is True
    assert client.in_draining_period is False

    # error_code / reason は受信した CONNECTION_CLOSE でのみ設定される
    # (ngtcp2 の rx.ccerr は受信フレームでのみ書き込まれる)。ローカルの
    # close() では設定されないため、非ゼロのエラーコードで閉じても None のまま
    assert client.error_code is None
    assert client.reason is None

    # サーバー側は close パケットが届かないため閉じられず、影響を受けない
    assert server.is_closed() is False
    assert server.in_closing_period is False
    assert server.in_draining_period is False
    assert server.error_code is None


def test_conn_state_remote_reflects_peer_config():
    """リモートのトランスポートパラメータがピアの設定値を反映する"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]
    # サーバーの設定をデフォルトと変え、ピアの値が反映されることを検証する
    server_config.max_streams_uni = 5
    server_config.max_data = 2_000_000

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)

    initial_packet = client.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    assert perform_handshake(client, server, initial_packet.data)

    # クライアントの remote_* がサーバーの設定値を反映する
    assert client.remote_initial_max_streams_uni == 5
    assert client.remote_initial_max_data == 2_000_000

    # サーバーの remote_* がクライアントの設定値 (デフォルト) を反映する
    assert server.remote_initial_max_streams_uni == 100
    assert server.remote_initial_max_data == DEFAULT_MAX_DATA

    # ローカルの設定は自身の値のまま (ピアの値と混同しない)
    assert client.local_initial_max_streams_uni == 100
    assert server.local_initial_max_streams_uni == 5


def test_conn_state_server_before_handshake():
    """accept 直後のサーバーも初期値が返る"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    initial_packet = client.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)

    # accept 直後はリモートパラメータ未受信で None
    assert server.remote_initial_max_data is None
    assert server.remote_max_idle_timeout is None
    assert server.error_code is None
    assert server.reason is None

    # TLS エラー / アラートは 0
    assert server.tls_error == 0
    assert server.tls_alert == 0

    # ネゴシエーションは未確定 (0)。クライアント選択は accept 時点で V1
    assert server.negotiated_version == 0
    assert server.client_chosen_version == QUIC_VERSION_V1

    # CLOSING / DRAINING は false、SCID は 1 個以上、アクティブ DCID は空
    assert server.in_closing_period is False
    assert server.in_draining_period is False
    assert len(server.scid) >= 1
    assert server.active_dcid == []


def test_tls_alert_on_certificate_verification_failure():
    """証明書検証失敗で接続が閉じられ TLS アラートが設定される"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.server_name = "localhost"
    # 証明書検証を常に失敗させる
    client_config.verify_callback = lambda certificates: False

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)

    initial_packet = client.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    server.receive(initial_packet.data, SERVER_ADDR, CLIENT_ADDR)

    # ハンドシェイクが失敗してクライアントが閉じるまでパケットを交換する
    for _ in range(20):
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)

        if client.is_closed():
            break

    # 証明書検証失敗でクライアントの接続が閉じられる
    assert client.is_closed()

    # TLS アラートが設定される (検証失敗のアラート)
    assert client.tls_alert != 0
    # tls_error はこの経路では設定されない場合があるため非ゼロは保証しない

    # ccerr は受信した CONNECTION_CLOSE でのみ設定されるため、ローカル側の
    # 証明書検証失敗では error_code / reason は None のまま
    assert client.error_code is None
    assert client.reason is None

    # サーバー側はクライアントの close パケットが届かないため閉じられない
    assert server.is_closed() is False
    assert server.error_code is None
