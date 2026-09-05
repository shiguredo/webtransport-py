"""テスト用フィクスチャ"""

import datetime
import ipaddress
import tempfile
from pathlib import Path
from typing import Protocol

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from webtransport import h2, h3
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


def _encode_varint(value: int) -> bytes:
    """RFC 9000 の可変長整数 (varint) をエンコードする

    先頭 2 ビットで長さを表す (00: 1 バイト / 01: 2 バイト /
    10: 4 バイト / 11: 8 バイト)。
    """
    if value < 0x40:
        return bytes([value])
    if value < 0x4000:
        return bytes([0x40 | (value >> 8), value & 0xFF])
    if value < 0x40000000:
        return (0x80000000 | value).to_bytes(4, "big")
    return (0xC000000000000000 | value).to_bytes(8, "big")


def _encode_capsule(capsule_type: int, payload: bytes) -> bytes:
    """Capsule Protocol のカプセルバイト列を組み立てる (RFC 9297 Section 3.2)

    Type / Length を RFC 9000 の可変長整数でエンコードするため、大きな
    値にも対応する。HTTP/2 DATA フレームのペイロードはカプセルバイト列
    そのもののため、ワイヤデータに対する部分列チェックで送出を検証できる。
    """
    return _encode_varint(capsule_type) + _encode_varint(len(payload)) + payload


def _encode_data_frame(session_id: int, payload: bytes = b"", end_stream: bool = False) -> bytes:
    """HTTP/2 DATA フレームのワイヤバイト列を組み立てる

    END_STREAM フラグ (0x01) 付きでピアがストリームを閉じた場合を再現する。
    h2 の公開 API に END_STREAM のみを送出する手段が存在しないため、
    ワイヤ注入で再現する。WT_CLOSE_SESSION 後の half-close や DATAGRAM
    capsule (Type 0x00) の送出検証にも使う。
    """
    flags = 0x01 if end_stream else 0x00
    return (
        len(payload).to_bytes(3, "big")
        + bytes([0x00, flags])
        + (session_id & 0x7FFFFFFF).to_bytes(4, "big")
        + payload
    )


def _encode_wt_datagram(session_id: int, payload: bytes) -> bytes:
    """WebTransport データグラムのワイヤ形式を組み立てる

    ワイヤ形式は Quarter Stream ID (セッション ID / 4) を RFC 9000 の
    可変長整数でエンコードしたもの + ペイロード。
    """
    quarter_stream_id = session_id // 4
    return _encode_varint(quarter_stream_id) + payload


def _encode_wt_stream_data(session_id: int, payload: bytes) -> bytes:
    """WT データストリーム (双方向) のワイヤ形式を組み立てる

    ワイヤ形式はストリームタイプ 0x41 (WT_STREAM_BIDI。RFC 9000 の
    可変長整数で 2 バイト 0x4041) + セッション ID (可変長整数) +
    ペイロード。テストで注入する受信側ストリームはピア起動の双方向
    ストリームを使う (クライアントなら %4==1、サーバーなら %4==0)。
    単方向ストリーム (0x54) は受信側のストリーム種別が限定されるため
    使わない。
    """
    return b"\x40\x41" + _encode_varint(session_id) + payload


def _setup_connect(
    client: h3.Session,
    server: h3.Session,
    connect_stream_id: int,
) -> bytes:
    """クライアントの CONNECT ヘッダーを取得し、QPACK/制御ストリームを渡す

    get_streams_to_send は 1 回の呼び出しで全てのデータを返すとは限らない
    (下記 _pump 参照) ため、データが無くなるまでループで取り出し、
    CONNECT ストリーム (connect_stream_id) 以外はすべて server に渡す。
    QPACK エンコーダー (6) のデータを渡し忘れるとサーバー側の QPACK
    デコードがブロックするため、全ストリームを渡し切る。
    """
    headers = None
    for _ in range(64):
        streams = client.get_streams_to_send()
        if not streams:
            break
        for stream_id, data, fin in streams:
            if stream_id == connect_stream_id:
                headers = data
            else:
                server.receive_stream_data(stream_id, data, fin)
    assert headers is not None, "CONNECT ヘッダーが取得できません"
    return headers


def _pump(src: h3.Session, dst: h3.Session) -> None:
    """src の送信データを全て dst に渡す

    QUIC レイヤーを介さず、get_streams_to_send で取り出したデータを
    receive_stream_data で直接渡す (モックなし)。get_streams_to_send は
    1 回の呼び出しで全てのデータを返すとは限らない (WT_CLOSE_SESSION 等は
    他のストリームの書き出し後に返る) ため、データが無くなるまで繰り返す。
    無限ループ防止のため最大 64 回で打ち切る。receive_stream_data が
    エラーを返した場合は Error イベントが dst に積まれる
    """
    for _ in range(64):
        sent = False
        for stream_id, data, fin in src.get_streams_to_send():
            dst.receive_stream_data(stream_id, data, fin)
            sent = True
        if not sent:
            break


def _create_session_pair() -> tuple[h3.Session, h3.Session]:
    """h3.Session のクライアント・サーバーペアを作成して初期化する

    @return (クライアント Session, サーバー Session)
    """
    client = h3.Session.create_client(h3.Config())
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)

    # ストリームをバインド (クライアントの単方向ストリームは %4 == 2、
    # サーバーは %4 == 3)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)
    server.set_max_client_streams_bidi(100)

    # サーバーの SETTINGS をクライアントに送る
    _pump(server, client)

    return client, server


def _accept_session(server: h3.Session) -> int:
    """サーバー側の SESSION_READY イベントを処理してセッションを受理する

    複数の SESSION_READY が積まれて 1 つの CONNECT に多重発火した場合は
    累積バグとしてテストを失敗させる (クライアント側の _drain_session_ready
    と対称)

    @return 受理したセッション ID
    """
    session_id = -1
    count = 0
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_READY:
            assert server.accept_session(event.session_id) is True, "セッションの受理に失敗しました"
            session_id = event.session_id
            count += 1
    assert count <= 1, "SESSION_READY が複数回発火しました"
    assert session_id >= 0, "セッションが確立されませんでした"
    return session_id


def _drain_session_ready(client: h3.Session) -> int:
    """クライアント側のイベントを全て読み出し、最後の SESSION_READY の
    セッション ID を返す (無ければ -1)。複数の SESSION_READY が積まれて
    いた場合は累積バグとしてテストを失敗させる
    """
    session_id = -1
    count = 0
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type == h3.EventType.SESSION_READY:
            session_id = event.session_id
            count += 1
    assert count <= 1, "SESSION_READY が複数回発火しました"
    return session_id


def _connect_session(
    client: h3.Session,
    server: h3.Session,
    stream_id: int,
) -> int:
    """クライアントが CONNECT を送信してセッションを確立する

    @param stream_id CONNECT に使うクライアント起動双方向ストリーム ID
    @return 確立したセッション ID
    """
    assert client.connect(stream_id, "https://localhost/webtransport") is True
    _pump(client, server)
    session_id = _accept_session(server)
    _pump(server, client)
    assert _drain_session_ready(client) == session_id
    return session_id


def _establish_session() -> tuple[h3.Session, h3.Session, int]:
    """h3.Session 同士で WebTransport セッションを確立する

    @return (クライアント Session, サーバー Session, セッション ID)
    """
    client, server = _create_session_pair()
    session_id = _connect_session(client, server, 0)
    return client, server, session_id


def _establish_two_sessions() -> tuple[h3.Session, h3.Session, int, int]:
    """h3.Session 同士で 2 つの WebTransport セッションを確立する

    @return (クライアント Session, サーバー Session, 1 つ目のセッション ID,
             2 つ目のセッション ID)
    """
    client, server = _create_session_pair()
    first_session_id = _connect_session(client, server, 0)
    second_session_id = _connect_session(client, server, 4)
    return client, server, first_session_id, second_session_id


def _h2_pump(src: h2.Session, dst: h2.Session) -> None:
    """h2.Session の src の送信データを全て dst に渡す

    QUIC レイヤーを介さず、send() で取り出したデータを receive() で直接
    渡す (モックなし)。send() は 1 回の呼び出しで送信バッファ全体を返し、
    空なら None を返すため、None が返るまで繰り返す (防御的に最大 64 回)。
    """
    for _ in range(64):
        sent = False
        while True:
            data = src.send()
            if data is None:
                break
            dst.receive(data)
            sent = True
        if not sent:
            break


def _create_h2_session_pair() -> tuple[h2.Session, h2.Session]:
    """h2.Session のクライアント・サーバーペアを作成して初期化する

    HTTP/2 preface + 双方の SETTINGS 交換まで完了させる
    (draft-15 Section 3.1 の is_webtransport_ready 成立まで)。

    @return (クライアント Session, サーバー Session)
    """
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server = h2.Session.create_server(server_config)

    # クライアントの preface + SETTINGS をサーバーへ
    _h2_pump(client, server)
    # サーバーの SETTINGS をクライアントへ
    _h2_pump(server, client)

    return client, server


def _connect_h2_session(
    client: h2.Session,
    server: h2.Session,
) -> int:
    """クライアントが CONNECT を送信して WebTransport セッションを確立する

    @return 確立したセッション ID
    """
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0, "CONNECT リクエストの送信に失敗しました"
    _h2_pump(client, server)

    # サーバー側で SESSION_READY が発火して受理できる
    ready_events = []
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == h2.EventType.SESSION_READY:
            ready_events.append(event)
    assert len(ready_events) == 1, "SESSION_READY が 0 回または複数回発火しました"
    assert ready_events[0].session_id == session_id
    assert server.accept_session(session_id) is True, "セッションの受理に失敗しました"
    _h2_pump(server, client)

    # クライアント側で SESSION_READY が発火する
    ready_events = []
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type == h2.EventType.SESSION_READY:
            ready_events.append(event)
    assert len(ready_events) == 1, "SESSION_READY が複数回発火しました"
    assert ready_events[0].session_id == session_id

    return session_id


class _EventSource[E](Protocol):
    """next_event() でイベントを 1 件ずつ取り出せるオブジェクト

    h3.Session / h2.Session / http2.Connection など、next_event() を
    持つオブジェクトが対象
    """

    def next_event(self) -> E | None: ...


def _drain_events[E](source: _EventSource[E]) -> list[E]:
    """イベントを全て取り出す (next_event() が None を返すまで)"""
    events = []
    while True:
        event = source.next_event()
        if event is None:
            break
        events.append(event)
    return events
