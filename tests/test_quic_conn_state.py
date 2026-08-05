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

from webtransport.quic import Config, Connection, EventType

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


def test_conn_state_after_retry():
    """サーバーが RETRY 要求を受けた接続は閉じた状態になり統計 API が None を返す"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)

    # クライアントの ClientHello は Initial 1 パケットの実効容量 (~1151 バイト) を
    # 超えるサイズ (現環境では約 1471 バイト) のため、複数の Initial パケットに
    # 分割されて送信される。分割の有無は TLS ライブラリのデフォルト設定に依存し
    # (主因は key_share の X25519MLKEM768)、崩れた場合は下の assert で明示的に
    # 失敗するため、RETRY 経路が無検証のまま通過することはない。
    first_initial = client.send()
    assert first_initial is not None
    second_initial = client.send()
    assert second_initial is not None, (
        "ClientHello が Initial 1 パケットに収まるため RETRY 経路を再現できない "
        "(ClientHello サイズが Initial パケットの実効容量を超える必要がある)"
    )

    server = Connection.accept(server_config, first_initial.data, SERVER_ADDR, CLIENT_ADDR)

    # accept は Initial パケットのヘッダーをデコードするだけで CRYPTO フレームは
    # 処理しない。そのため 2 つ目の Initial (オフセット非ゼロの CRYPTO を含む) を
    # 受信すると、オフセット 0 のデータが未処理のまま非ゼロ分だけがバッファリング
    # され、ngtcp2 はアドレス検証の要求として NGTCP2_ERR_RETRY を返す
    # (ngtcp2_conn_read_pkt のドキュメント: Server must perform address validation
    # by sending Retry packet [...] and discard the connection state.)。
    assert server.receive(second_initial.data, SERVER_ADDR, CLIENT_ADDR) == 0, (
        "2 つ目の Initial 受信で RETRY 経路に入らず、パケット正常処理 "
        "(data.size() を返す) または別のエラー経路に入った"
    )

    # 本ライブラリには Retry パケット送出手段が無いため接続は継続不能として
    # 扱われ、ConnectionClosed イベントが push される。イベントは終了系共通の
    # 合成値 (error_code 0 / stream_id -1 / fin false) になる
    event = server.next_event()
    assert event is not None
    assert event.type == EventType.CONNECTION_CLOSED
    assert event.reason == "retry required"
    assert event.error_code == 0
    assert event.stream_id == -1
    assert event.fin is False
    assert server.next_event() is None

    # 閉じた状態のため is_closed() は true、確立状態は false のまま。
    # RETRY は ngtcp2 の状態機械を CLOSING / DRAINING に遷移させないため、
    # in_closing_period / in_draining_period は close() 後と異なり false のまま
    assert server.is_closed() is True
    assert server.is_established() is False
    assert server.in_closing_period is False
    assert server.in_draining_period is False

    # ccerr は受信した CONNECTION_CLOSE でのみ設定されるため、RETRY 後も
    # error_code / reason は None のまま
    assert server.error_code is None
    assert server.reason is None

    # 接続統計 API は閉じている場合は None を返す契約どおりに動作する
    # (ngtcp2_conn_get_conn_info を呼ばず nullopt になる)。全項目の検証は
    # 接続統計テスト (test_quic_conn_stats.py) が担うため、ここでは
    # get_conn_info 経由と独立した closed_ ガード (pto) の代表を確認する
    assert server.cwnd is None
    assert server.latest_rtt is None
    assert server.min_rtt is None
    assert server.smoothed_rtt is None
    assert server.bytes_in_flight is None
    assert server.pkt_sent is None
    assert server.pkt_recv is None
    assert server.pto is None

    # タイムアウトも閉じているため取得できない
    assert server.get_timeout() is None

    # 以後の送受信は無効になる (receive は 0 を返し、send は None)。
    # 2 つ目の Initial を再送しても closed_ ガードで処理されず 0 が返り、
    # 新たなイベントも push されない (修正前は RETRY が再発して 2 つ目の
    # ConnectionClosed イベントが push されていた)
    assert server.receive(second_initial.data, SERVER_ADDR, CLIENT_ADDR) == 0
    assert server.next_event() is None
    assert server.send() is None


def test_close_delivers_connection_close():
    """close() が生成した CONNECTION_CLOSE をピアが受信して error_code / reason と DRAINING になる"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # クライアント側の HANDSHAKE_COMPLETED イベントを消費しておく
    while client.next_event() is not None:
        pass

    # サーバーが非ゼロの error_code で close() する。サーバーはハンドシェイク
    # 完了で確認状態 (HANDSHAKE_CONFIRMED) になり 1-RTT パケットで
    # CONNECTION_CLOSE を送るため、error_code が指定値のまま届く
    server.close(0x100, "server error")
    assert server.is_closed()

    # close() 後、send() が CONNECTION_CLOSE パケットを 1 回だけ返す
    close_packet = server.send()
    assert close_packet is not None
    assert server.send() is None

    # ピア (クライアント) が CONNECTION_CLOSE を受信すると、ccerr が設定され
    # て error_code / reason が取得でき、DRAINING 状態になる
    # (ピアの CONNECTION_CLOSE 受信で DRAINING 状態に入る (RFC 9000
    # Section 10.2.2))
    client.receive(close_packet.data, CLIENT_ADDR, SERVER_ADDR)
    assert client.is_closed() is True
    assert client.in_draining_period is True
    assert client.error_code == 0x100
    assert client.reason == "server error"

    # CONNECTION_CLOSED イベントが push される。終了系共通の合成値になる
    event = client.next_event()
    assert event is not None
    assert event.type == EventType.CONNECTION_CLOSED
    assert event.reason == "connection draining"
    assert event.error_code == 0
    assert event.stream_id == -1
    assert event.fin is False


def test_client_close_delivers_connection_close():
    """クライアント側 close() が生成した CONNECTION_CLOSE もピアに届く"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 両側の HANDSHAKE_COMPLETED イベントを消費しておく
    while client.next_event() is not None:
        pass
    while server.next_event() is not None:
        pass

    # クライアントが非ゼロの error_code で close() する。ハンドシェイク
    # 完了後に呼んでいるため 1-RTT パケットで CONNECTION_CLOSE が送られ、
    # error_code が指定値のまま届く
    client.close(0x2A, "client error")
    close_packet = client.send()
    assert close_packet is not None
    assert client.send() is None

    # ピア (サーバー) が CONNECTION_CLOSE を受信して DRAINING 状態になる
    server.receive(close_packet.data, SERVER_ADDR, CLIENT_ADDR)
    assert server.is_closed() is True
    assert server.in_draining_period is True
    assert server.error_code == 0x2A
    assert server.reason == "client error"

    # CONNECTION_CLOSED イベントが push される
    event = server.next_event()
    assert event is not None
    assert event.type == EventType.CONNECTION_CLOSED
    assert event.reason == "connection draining"


def test_close_before_handshake():
    """ハンドシェイク前の close() はクラッシュせず is_closed() になる"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    # クライアント Initial 未送信の close() (ngtcp2 の状態は
    # NGTCP2_CS_CLIENT_INITIAL のため CONNECTION_CLOSE を書けない)
    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    client.close(0x100, "client close before initial")
    assert client.is_closed() is True
    # パケットは生成されないため in_closing_period は false のまま
    assert client.in_closing_period is False
    assert client.send() is None

    # サーバー Initial 未受信の close() (accept 直後で receive 前)
    client2 = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    initial_packet = client2.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    server.close(0x100, "server close before initial")
    assert server.is_closed() is True
    # accept 直後は ngtcp2 が Initial 鍵を持っていないためパケット無し
    assert server.in_closing_period is False
    assert server.send() is None


def test_close_mid_handshake_replaces_error_code():
    """ハンドシェイク途中の close() は CONNECTION_CLOSE を生成するが error_code が置換される"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.verify_peer = False
    client_config.server_name = "localhost"

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)

    # クライアントの Initial をサーバーに渡して Initial 交換済みの状態にする
    # (ハンドシェイクは未完了)
    first_initial = client.send()
    assert first_initial is not None
    server = Connection.accept(server_config, first_initial.data, SERVER_ADDR, CLIENT_ADDR)
    server.receive(first_initial.data, SERVER_ADDR, CLIENT_ADDR)
    second_initial = client.send()
    if second_initial:
        server.receive(second_initial.data, SERVER_ADDR, CLIENT_ADDR)
    assert server.is_handshake_completed() is False

    # ハンドシェイク途中の close() は Initial または Handshake パケットで
    # CONNECTION_CLOSE を生成できるため in_closing_period は true になる
    server.close(0x100, "mid handshake close")
    assert server.is_closed() is True
    assert server.in_closing_period is True
    close_packet = server.send()
    assert close_packet is not None

    # Initial パケットの CONNECTION_CLOSE では error_code が
    # APPLICATION_ERROR (0x0c) に置換され reason が落ちる
    # (RFC 9000 Section 10.2.3)
    client.receive(close_packet.data, CLIENT_ADDR, SERVER_ADDR)
    assert client.is_closed() is True
    assert client.in_draining_period is True
    assert client.error_code == 0x0C
    assert client.reason == ""
