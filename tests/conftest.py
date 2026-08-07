"""テスト用フィクスチャ"""

import datetime
import ipaddress
import tempfile
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from webtransport.quic import Config, Connection


@pytest.fixture(scope="session")
def test_certificates():
    """テスト用の自己署名証明書を生成する

    Returns:
        dict: certfile と keyfile のパスを含む辞書
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        certfile = tmpdir_path / "cert.pem"
        keyfile = tmpdir_path / "key.pem"

        private_key = ec.generate_private_key(ec.SECP256R1())

        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COUNTRY_NAME, "JP"),
                x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Tokyo"),
                x509.NameAttribute(NameOID.LOCALITY_NAME, "Chiyoda"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test"),
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
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
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

        yield {
            "certfile": str(certfile),
            "keyfile": str(keyfile),
        }


# Sans-IO テスト用の固定パスアドレス
CLIENT_ADDR = ("127.0.0.1", 50000)
SERVER_ADDR = ("127.0.0.1", 4433)


def create_test_certificates():
    """テスト用の自己署名証明書を生成

    QUIC の Sans-IO テストは実通信 (実ソケットを使用しないパケット交換) の
    ため、接続先アドレスは固定値を使う。証明書は ECDSA P-256 の自己署名
    証明書で、ハンドシェイクの検証は verify_peer=False のため内容は問われない。
    """
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
    """QUIC のクライアントとサーバーのペアを作成"""
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


def perform_handshake(client: Connection, server: Connection, initial_packet: bytes) -> bool:
    """QUIC ハンドシェイクを完了させる"""
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
