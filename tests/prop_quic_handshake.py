"""QUIC Sans I/O API の高度な Property-Based Testing

QUIC ハンドシェイクと通信のテスト
"""

import datetime
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from webtransport.quic import Config, Connection, EventType

# Sans-IO テスト用の固定パスアドレス
CLIENT_ADDR: tuple[str, int] = ("127.0.0.1", 50000)
SERVER_ADDR: tuple[str, int] = ("127.0.0.1", 4433)


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
    server = Connection.accept(
        server_config,
        initial_packet.data,
        SERVER_ADDR,
        CLIENT_ADDR,
    )

    return client, server, initial_packet.data


def perform_handshake(client: Connection, server: Connection, initial_packet: bytes) -> bool:
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


@given(st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789", min_size=1, max_size=50))
@settings(max_examples=20)
def prop_quic_handshake_with_server_name(server_name: str):
    """任意のサーバー名でハンドシェイクが成功する"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = server_name

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    initial_packet = client.send()

    if initial_packet is None:
        return

    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    if server is None:
        return

    success = perform_handshake(client, server, initial_packet.data)
    assert success, "Handshake should complete"


@given(
    st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=20),
        min_size=1,
        max_size=5,
    )
)
@settings(max_examples=20)
def prop_quic_handshake_with_alpn(protocols: list[str]):
    """任意の ALPN プロトコルでハンドシェイクが試行できる"""
    client_config = Config()
    client_config.alpn_protocols = protocols
    client_config.verify_peer = False
    client_config.server_name = "localhost"

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = protocols

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    initial_packet = client.send()

    if initial_packet is None:
        return

    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    if server is None:
        return

    perform_handshake(client, server, initial_packet.data)


@given(st.binary(min_size=1, max_size=16384))
@settings(max_examples=30)
def prop_quic_stream_data_after_handshake(data: bytes):
    """ハンドシェイク後に任意のストリームデータを送信できる"""
    client, server, initial_packet = create_client_server_pair()
    success = perform_handshake(client, server, initial_packet)
    assume(success)

    stream_id = client.open_stream(True)
    if stream_id < 0:
        return

    client.send_stream_data(stream_id, data, True)

    client_packet = client.send()
    if client_packet:
        server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)

    received_data = b""
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == EventType.STREAM_DATA:
            received_data += event.data

    if received_data:
        assert received_data == data


@given(st.binary(min_size=1, max_size=1200))
@settings(max_examples=30)
def prop_quic_datagram_after_handshake(data: bytes):
    """ハンドシェイク後に任意のデータグラムを送信できる"""
    client, server, initial_packet = create_client_server_pair()
    success = perform_handshake(client, server, initial_packet)
    assume(success)

    client.send_datagram(data)

    client_packet = client.send()
    if client_packet:
        server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)


@given(st.integers(min_value=1, max_value=10))
@settings(max_examples=20)
def prop_quic_multiple_streams(num_streams: int):
    """ハンドシェイク後に複数のストリームを開ける"""
    client, server, initial_packet = create_client_server_pair()
    success = perform_handshake(client, server, initial_packet)
    assume(success)

    stream_ids = []
    for _ in range(num_streams):
        stream_id = client.open_stream(True)
        if stream_id >= 0:
            stream_ids.append(stream_id)

    assert len(stream_ids) == num_streams


@given(
    st.lists(
        st.tuples(st.binary(min_size=1, max_size=500), st.booleans()),
        min_size=1,
        max_size=5,
    )
)
@settings(max_examples=20)
def prop_quic_interleaved_stream_data(stream_data_list: list[tuple[bytes, bool]]):
    """複数ストリームへのインターリーブ送信が動作する"""
    client, server, initial_packet = create_client_server_pair()
    success = perform_handshake(client, server, initial_packet)
    assume(success)

    stream_ids = []
    for _ in range(len(stream_data_list)):
        stream_id = client.open_stream(True)
        if stream_id >= 0:
            stream_ids.append(stream_id)

    if len(stream_ids) != len(stream_data_list):
        return

    for stream_id, (data, fin) in zip(stream_ids, stream_data_list):
        client.send_stream_data(stream_id, data, fin)

    for _ in range(5):
        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)

        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

        if not client_packet and not server_packet:
            break


@given(st.integers(min_value=0, max_value=2**62 - 1), st.text(max_size=100))
@settings(max_examples=30)
def prop_quic_close_after_handshake(error_code: int, reason: str):
    """ハンドシェイク後に任意のエラーコードで接続をクローズできる"""
    client, server, initial_packet = create_client_server_pair()
    success = perform_handshake(client, server, initial_packet)
    assume(success)

    client.close(error_code, reason)

    client_packet = client.send()
    if client_packet:
        server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)


@given(st.integers(min_value=0, max_value=2**62 - 1))
@settings(max_examples=30)
def prop_quic_idle_timeout_config(timeout_ns: int):
    """任意のアイドルタイムアウト値を設定できる (QUIC varint 範囲)"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"
    client_config.idle_timeout_ns = timeout_ns

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    assert client is not None


@given(st.integers(min_value=0, max_value=65535))
@settings(max_examples=30)
def prop_quic_max_datagram_frame_size_config(size: int):
    """任意の最大データグラムフレームサイズを設定できる"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"
    client_config.max_datagram_frame_size = size

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    assert client is not None
