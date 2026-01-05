"""QUIC ハンドシェイクのデバッグテスト"""

import tempfile
import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID


def create_test_certificates():
    """テスト用の自己署名証明書を生成する (シンプル版)"""
    tmpdir_path = Path(tempfile.mkdtemp())
    certfile = tmpdir_path / "cert.pem"
    keyfile = tmpdir_path / "key.pem"

    private_key = ec.generate_private_key(ec.SECP256R1())

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )

    # シンプルな証明書 (拡張なし)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.timezone.utc))
        .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1))
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


def test_quic_lowlevel_handshake():
    """低レベル QUIC API ハンドシェイクテスト"""
    from webtransport.quic import Config, Connection

    certfile, keyfile = create_test_certificates()

    print("\n=== Starting QUIC handshake test ===")

    # クライアント設定
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"

    # サーバー設定
    server_config = Config()
    server_config.cert_file = certfile
    server_config.key_file = keyfile
    server_config.alpn_protocols = ["h3"]

    # クライアント接続を作成
    print("Creating client connection...")
    client = Connection.create_client(client_config)
    assert client is not None
    print(
        f"Client created. is_established: {client.is_established()}, is_handshake_completed: {client.is_handshake_completed()}"
    )

    # クライアントの最初のパケットを取得
    print("\nClient send()...")
    initial_packet = client.send()
    assert initial_packet is not None
    print(f"Client initial packet: {len(initial_packet)} bytes")
    print(f"Initial packet hex (first 64 bytes): {initial_packet[:64].hex()}")

    # サーバー接続を作成 (accept)
    print("\nCreating server connection from initial packet...")
    server = Connection.accept(server_config, initial_packet)
    assert server is not None
    print(
        f"Server created. is_established: {server.is_established()}, is_handshake_completed: {server.is_handshake_completed()}"
    )

    # サーバーで初期パケットを処理
    print("\nServer receive()...")
    result = server.receive(initial_packet)
    print(f"Server receive result: {result}")
    print(
        f"Server is_established: {server.is_established()}, is_handshake_completed: {server.is_handshake_completed()}"
    )

    # ハンドシェイクループ
    max_iterations = 20
    for iteration in range(max_iterations):
        print(f"\n--- Iteration {iteration} ---")

        # サーバーからデータを取得
        server_packet = server.send()
        if server_packet:
            print(f"Server sends {len(server_packet)} bytes")
            print(f"Packet hex (first 32 bytes): {server_packet[:32].hex()}")

            # クライアントで受信
            print("Client receive()...")
            result = client.receive(server_packet)
            print(f"Client received result: {result}")
            print(
                f"Client is_established: {client.is_established()}, is_handshake_completed: {client.is_handshake_completed()}"
            )
            if result == 0:
                print("Client receive failed!")
                # イベントを確認
                while True:
                    event = client.next_event()
                    if event is None:
                        break
                    print(f"  Client event: {event.type}")
                break
        else:
            print("Server has no data to send")

        # クライアントからデータを取得
        client_packet = client.send()
        if client_packet:
            print(f"Client sends {len(client_packet)} bytes")

            # サーバーで受信
            print("Server receive()...")
            result = server.receive(client_packet)
            print(f"Server received result: {result}")
            print(
                f"Server is_established: {server.is_established()}, is_handshake_completed: {server.is_handshake_completed()}"
            )
            if result == 0:
                print("Server receive failed!")
                break
        else:
            print("Client has no data to send")

        # ハンドシェイク完了チェック
        if client.is_handshake_completed() and server.is_handshake_completed():
            print("\n=== Handshake completed! ===")
            break

    # 最終状態確認
    print(f"\nFinal state:")
    print(
        f"  Client: is_established={client.is_established()}, is_handshake_completed={client.is_handshake_completed()}"
    )
    print(
        f"  Server: is_established={server.is_established()}, is_handshake_completed={server.is_handshake_completed()}"
    )

    # 成功を確認
    assert client.is_handshake_completed(), "Client handshake not completed"
    assert server.is_handshake_completed(), "Server handshake not completed"


if __name__ == "__main__":
    test_quic_lowlevel_handshake()
