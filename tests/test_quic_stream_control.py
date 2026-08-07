"""QUIC ストリーム・接続制御 API のテスト

ストリーム上限確認・keep-alive・鍵更新・フロー制御の動的拡張 API の動作を
確認する。実通信構成 (モックなし) で、ピア側の観測値 (フロー制御残量等) も
検証する。
"""

import time

from conftest import (
    CLIENT_ADDR,
    SERVER_ADDR,
    create_client_server_pair,
    perform_handshake,
)

from webtransport.quic import Config, Connection

# ngtcp2 が keep-alive の無効化に使う値
UINT64_MAX = (1 << 64) - 1

# 既定設定のフロー制御ウィンドウ (Config のデフォルト値)
# MAX_DATA はコネクション全体 (1 MiB)、MAX_STREAM_DATA はストリームごと (256 KiB)。
# フレームの送出条件 (未送出の拡張量が window/4 を超えた場合) の確認に使う
MAX_DATA_DEFAULT = 1048576
MAX_STREAM_DATA_DEFAULT = 262144
# 既定の最大ストリーム数 (Config のデフォルト値)
MAX_STREAMS_DEFAULT = 100


def drain_timers(client: Connection, server: Connection):
    """ACK 遅延・PTO 等のタイマーを消化して静穏状態にする

    ハンドシェイク完了直後は、未 ACK の ack-eliciting パケットに対する PTO や
    遅延 ACK のタイマーが残っており、get_timeout() が keep-alive 以外の
    タイマーを先に返す。送信すべきパケットが無くなるまで送受信を繰り返し、
    get_timeout() がアイドルタイムアウト (30 秒) のみを返す状態にする。
    keep-alive が有効な間は期限 (1 秒未満) が返り続けて静穏条件に達しない
    ため、呼び出す前に keep_alive_timeout(UINT64_MAX) で無効化すること。
    """
    for _ in range(20):
        client_timeout = client.get_timeout()
        server_timeout = server.get_timeout()

        if client_timeout is not None and client_timeout <= 0:
            client.handle_timeout()
            client_packet = client.send()
            if client_packet:
                server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
        if server_timeout is not None and server_timeout <= 0:
            server.handle_timeout()
            server_packet = server.send()
            if server_packet:
                client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

        # 両側のタイマーがアイドルタイムアウト相当 (1 秒超) になったら静穏とみなす
        if (client_timeout is None or client_timeout > 1_000_000_000) and (
            server_timeout is None or server_timeout > 1_000_000_000
        ):
            return
        time.sleep(0.05)


def exchange_packets(client: Connection, server: Connection, rounds: int = 10):
    """双方向のパケット送受信を指定ラウンド分交換する"""
    for _ in range(rounds):
        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
        if client_packet is None and server_packet is None:
            return


def test_streams_left_before_handshake():
    """ハンドシェイク前は残りストリーム数が 0 (未確定) になる"""
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.server_name = "localhost"
    client_config.verify_peer = False

    # send() 前の状態を確認するため、クライアントを直接生成する
    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)

    # ハンドシェイク前はピアのトランスポートパラメータが未受信のため 0 を返す
    # (ngtcp2 の初期値 max_streams が 0 であることに依存する)
    assert client.streams_bidi_left == 0
    assert client.streams_uni_left == 0


def test_streams_left_after_handshake():
    """ハンドシェイク後はピアの広告値が反映され、開くと減り閉じても戻らない"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # ピア (サーバー) の広告値 (Config のデフォルト 100) が反映される
    assert client.streams_bidi_left == MAX_STREAMS_DEFAULT
    assert client.streams_uni_left == MAX_STREAMS_DEFAULT

    # ストリームを開くと残数が減る
    bidi_stream_id = client.open_stream(True)
    assert bidi_stream_id >= 0
    uni_stream_id = client.open_stream(False)
    assert uni_stream_id >= 0
    assert client.streams_bidi_left == MAX_STREAMS_DEFAULT - 1
    assert client.streams_uni_left == MAX_STREAMS_DEFAULT - 1

    # ストリームを閉じても残数は戻らない (RFC 9000 Section 19.11 の累積制限)
    client.close_stream(bidi_stream_id, 0)
    client.close_stream(uni_stream_id, 0)
    assert client.streams_bidi_left == MAX_STREAMS_DEFAULT - 1
    assert client.streams_uni_left == MAX_STREAMS_DEFAULT - 1


def test_keep_alive_timeout():
    """keep-alive を設定すると期限が get_timeout に反映され、期限超過で PING が送出される"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 静穏状態 (ACK 遅延・PTO 等のタイマーを消化し、アイドルタイムアウトのみ残す)
    drain_timers(client, server)

    # keep-alive を 1 秒に設定すると、get_timeout がアイドルタイムアウト
    # (既定 30 秒) より早い keep-alive の期限を返す
    client.keep_alive_timeout(1_000_000_000)
    timeout = client.get_timeout()
    assert timeout is not None
    assert timeout <= 1_000_000_000

    # 期限超過後に handle_timeout → send() で PING が送出され、
    # ピア側の ping_recv (0014 の接続統計) が増加する
    ping_recv_before = server.ping_recv
    time.sleep(1.1)
    client.handle_timeout()
    client_packet = client.send()
    assert client_packet is not None
    server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
    assert server.ping_recv > ping_recv_before

    # keep-alive を無効化してから静穏状態に戻す。keep-alive 有効のまま
    # drain_timers を回すと期限切れの PING が送出され続けて静穏条件
    # (タイマー 1 秒超) に達せず、未 ACK の PING の PTO が残って後段の
    # 検証をフレークさせるため、先に無効化する
    client.keep_alive_timeout(UINT64_MAX)
    drain_timers(client, server)

    # 無効化後は期限超過でも PING が送出されなくなる
    ping_recv_after_ping = server.ping_recv
    time.sleep(1.1)
    client.handle_timeout()
    assert client.send() is None
    assert server.ping_recv == ping_recv_after_ping


def test_initiate_key_update_before_handshake():
    """ハンドシェイク完了前は鍵更新を開始できない"""
    client, server, _ = create_client_server_pair()

    # ハンドシェイク前はクライアント・サーバーとも False
    # (ngtcp2 内部の assert 回避のためのガードが False を返す)
    assert client.initiate_key_update() is False
    assert server.initiate_key_update() is False


def test_initiate_key_update_after_handshake():
    """ハンドシェイク完了後に鍵更新を開始できる (連続呼び出しは 2 回目が False)"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # ハンドシェイク完了直後は、クライアントは post-handshake の write 前、
    # サーバーは post-handshake の read / write 前のため False
    assert client.initiate_key_update() is False
    assert server.initiate_key_update() is False

    # クライアントが 1RTT パケット (データ) を書き出し、サーバーがそれを
    # 受信・応答すると、両側で post-handshake の state 遷移と新鍵準備が完了する
    stream_id = client.open_stream(True)
    client.send_stream_data(stream_id, b"key update trigger")
    exchange_packets(client, server)

    assert client.initiate_key_update() is True
    assert server.initiate_key_update() is True

    # 鍵更新確認前の連続呼び出しは 2 回目が False になる
    # (RFC 9001 Section 6.1 の MUST。ngtcp2 は鍵更新未確認フラグで実装)
    assert client.initiate_key_update() is False
    assert server.initiate_key_update() is False


def test_extend_max_offset():
    """コネクション全体のフロー制御を拡張できる (閾値未満は送出されない)"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 閾値未満 (window/4 = 256 KiB 以下) の拡張は MAX_DATA フレームが送出されず、
    # ピア側の max_data_left は変化しない
    server.extend_max_offset(1_000)
    exchange_packets(client, server)
    assert client.max_data_left == MAX_DATA_DEFAULT

    # 閾値を超える累積拡張 (300,000 > 256 KiB) で MAX_DATA フレームが送出され、
    # ピア側の max_data_left が拡張量ぶん増加する
    server.extend_max_offset(300_000)
    exchange_packets(client, server)
    assert client.max_data_left == MAX_DATA_DEFAULT + 301_000


def test_extend_max_stream_offset():
    """ストリームのフロー制御を拡張できる (ローカル単方向は False)"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # クライアントがストリームを開いてデータを送ると、サーバーがストリームの
    # 存在を認識する (MAX_STREAM_DATA の送出対象になる)。サーバーは受信した
    # データ量ぶんを自動再開放するため、送信した 1 バイト分も未送出の拡張量に
    # 含まれる
    stream_id = client.open_stream(True)
    client.send_stream_data(stream_id, b"x")
    exchange_packets(client, server)

    # 閾値未満 (window/4 = 64 KiB 以下) の拡張は送出されない
    # (自動再開放の 1 バイトを含めても閾値未満)
    server.extend_max_stream_offset(stream_id, 10_000)
    exchange_packets(client, server)
    assert client.max_stream_data_left(stream_id) == MAX_STREAM_DATA_DEFAULT - 1

    # 閾値を超える拡張 (100,000 > 64 KiB) で送出され、ピア側の残量が増加する。
    # 閾値判定は未送出の累積拡張量で行われるため、保留中の 10,000 と
    # 自動再開放の 1 バイトも同時に送出される (110,001 ぶんの増加)
    server.extend_max_stream_offset(stream_id, 100_000)
    exchange_packets(client, server)
    assert client.max_stream_data_left(stream_id) == MAX_STREAM_DATA_DEFAULT - 1 + 110_001

    # 存在しないストリーム ID (ローカル単方向を除く) には 0 (成功) が返る。
    # 9,998 % 4 = 2 (bit 0 = 0 でクライアント発起・bit 1 = 1 で単方向) であり、
    # サーバーのローカル単方向 (bit 0 = 1 かつ bit 1 = 1、mod 4 = 3) ではない
    assert server.extend_max_stream_offset(9_998, 1_000) is True

    # ローカル単方向ストリーム ID (存在の有無を問わず、負値の単方向 ID も含む)
    # には NGTCP2_ERR_INVALID_ARGUMENT (False) が返る
    assert server.extend_max_stream_offset(3, 1_000) is False
    assert server.extend_max_stream_offset(-1, 1_000) is False


def test_extend_max_streams():
    """ストリーム上限を拡張できる (ピアの残りストリーム数が増加する)"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # MAX_STREAMS は未送出の拡張があれば送出され、ピア側の残りストリーム数が
    # 増加する (RFC 9000 Section 19.11 の累積制限)
    server.extend_max_streams_bidi(50)
    server.extend_max_streams_uni(50)
    exchange_packets(client, server)
    assert client.streams_bidi_left == MAX_STREAMS_DEFAULT + 50
    assert client.streams_uni_left == MAX_STREAMS_DEFAULT + 50


def test_stream_control_after_close():
    """接続を閉じた後は getter が None、setter / mutator が no-op / False になる"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 接続を閉じる
    client.close(0, "normal close")
    assert client.is_closed()

    # getter は None を返す
    assert client.streams_bidi_left is None
    assert client.streams_uni_left is None

    # mutator は no-op / False を返す
    client.keep_alive_timeout(1_000_000_000)
    assert client.initiate_key_update() is False
    client.extend_max_offset(1_000)
    assert client.extend_max_stream_offset(0, 1_000) is False
    client.extend_max_streams_bidi(50)
    client.extend_max_streams_uni(50)
