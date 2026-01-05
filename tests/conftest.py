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
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
            .not_valid_after(
                datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
            )
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
