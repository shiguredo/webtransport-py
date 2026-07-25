"""QUIC エラー処理のテスト

ngtcp2 API のエラー処理が正しく動作することを確認するテスト
"""

import tempfile
import datetime
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from webtransport.quic import Config, Connection


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

    client = Connection.create_client(client_config)

    initial_packet = client.send()
    server = Connection.accept(server_config, initial_packet)

    return client, server, initial_packet


def perform_handshake(client: Connection, server: Connection, initial_packet: bytes):
    """ハンドシェイクを完了させる"""
    server.receive(initial_packet)

    for _ in range(20):
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet)

        client_packet = client.send()
        if client_packet:
            server.receive(client_packet)

        if client.is_handshake_completed() and server.is_handshake_completed():
            return True

        if not server_packet and not client_packet:
            break

    return False


def test_close_nonexistent_stream():
    """存在しないストリームをクローズしてもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 存在しないストリーム ID でクローズを試みる
    nonexistent_stream_id = 9999
    client.close_stream(nonexistent_stream_id, 0)

    # 接続は閉じられていないこと
    assert not client.is_closed()


def test_send_data_to_closed_stream():
    """クローズしたストリームにデータを送信"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    stream_id = client.open_stream(True)
    assert stream_id >= 0

    # ストリームをクローズ
    client.close_stream(stream_id, 0)

    # クローズしたストリームにデータを送信
    client.send_stream_data(stream_id, b"test data", False)

    # send() を呼んでもクラッシュしない
    client.send()

    # 接続は閉じられていないこと
    assert not client.is_closed()


def test_connection_draining_state():
    """接続がドレイン状態になった場合のエラー処理"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # サーバーから接続をクローズ
    # close() は内部でクローズパケットを生成し closed_ フラグを立てる
    server.close(0, "normal close")

    # close() 後は closed_ が true なので send() は None を返す
    # これは期待される動作
    assert server.is_closed()

    # クライアント側では、サーバーからの明示的なクローズパケットがなくても
    # タイムアウトで接続が閉じられる
    # ここでは close() が正しく closed_ フラグを設定することを確認
    assert not client.is_closed()  # クライアントはまだ閉じていない


def test_receive_after_close():
    """接続クローズ後にパケットを受信してもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # サーバーがパケットを生成
    server_packet = server.send()

    # クライアントが接続をクローズ
    client.close(0, "client close")

    # クローズ後にパケットを受信
    if server_packet:
        result = client.receive(server_packet)
        # クラッシュしないことを確認
        assert result == 0  # クローズ後は 0 バイト処理


def test_send_after_close():
    """接続クローズ後に send() を呼んでもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 接続をクローズ
    # close() は内部でクローズパケットを生成し closed_ フラグを立てる
    client.close(0, "normal close")

    # close() 後は closed_ が true
    assert client.is_closed()

    # close() 後に send() を呼ぶと None が返る（期待される動作）
    result = client.send()
    assert result is None

    # さらに send() を呼んでも None が返る
    result = client.send()
    assert result is None


def test_stream_data_after_fin():
    """FIN 送信後にデータを送信"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    stream_id = client.open_stream(True)
    assert stream_id >= 0

    # FIN フラグ付きでデータを送信
    client.send_stream_data(stream_id, b"final data", True)

    # パケットを送信
    packet = client.send()
    if packet:
        server.receive(packet)

    # FIN 後にさらにデータを送信しようとする
    client.send_stream_data(stream_id, b"more data", False)

    # send() を呼んでもクラッシュしない
    client.send()

    # 接続は閉じられていないこと
    assert not client.is_closed()


def test_multiple_close_calls():
    """接続を複数回クローズしてもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 複数回クローズを呼ぶ
    client.close(0, "first close")
    client.close(1, "second close")
    client.close(2, "third close")

    # クラッシュしないことを確認
    assert client.is_closed()


def test_multiple_stream_close_calls():
    """同じストリームを複数回クローズしてもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    stream_id = client.open_stream(True)
    assert stream_id >= 0

    # 同じストリームを複数回クローズ
    client.close_stream(stream_id, 0)
    client.close_stream(stream_id, 1)
    client.close_stream(stream_id, 2)

    # 接続は閉じられていないこと
    assert not client.is_closed()


def test_handle_timeout_without_activity():
    """アクティビティなしでタイムアウト処理を呼んでもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # タイムアウト処理を複数回呼ぶ
    for _ in range(10):
        client.handle_timeout()
        server.handle_timeout()

    # 接続は閉じられていないこと
    assert not client.is_closed()
    assert not server.is_closed()


def test_open_stream_after_close():
    """接続クローズ後にストリームを開こうとしても -1 が返る"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 接続をクローズ
    client.close(0, "normal close")

    # クローズ後にストリームを開こうとする
    stream_id = client.open_stream(True)
    assert stream_id == -1


def test_datagram_after_close():
    """接続クローズ後にデータグラムを送信してもクラッシュしない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 接続をクローズ
    client.close(0, "normal close")

    # クローズ後にデータグラムを送信
    client.send_datagram(b"test datagram")

    # send() を呼んでもクラッシュしない
    result = client.send()
    # クローズパケットまたは None が返る
    assert result is None or isinstance(result, bytes)
