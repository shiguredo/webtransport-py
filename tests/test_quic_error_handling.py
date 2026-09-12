"""QUIC エラー処理のテスト

ngtcp2 API のエラー処理が正しく動作することを確認するテスト
"""

import time

from conftest import (
    CERTFILE,
    CLIENT_ADDR,
    KEYFILE,
    PUMP_ATTEMPTS,
    SERVER_ADDR,
    create_client_server_pair,
    perform_handshake,
    wait_pacing_timeout,
)

from webtransport.quic import Config, Connection, EventType, Packet, ReceiveResult


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
    # close() は CONNECTION_CLOSE パケットを生成して closed_ フラグを立てる
    server.close(0, "normal close")

    # close() 後は closed_ が true になり、send() が CONNECTION_CLOSE を返す
    assert server.is_closed()

    # クライアント側では、サーバーからの明示的なクローズパケットがなくても
    # タイムアウトで接続が閉じられる
    # ここでは close() が正しく closed_ フラグを設定することを確認
    assert not client.is_closed()  # クライアントはまだ閉じていない


def test_receive_after_close():
    """接続クローズ後にパケットを受信すると CONNECTION_CLOSE が再アームされる"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # サーバーがパケットを生成
    server_packet = _send_with_pacing_wait(server)
    assert server_packet is not None, "pacing 待ち後もサーバーのパケットを生成できません"

    # クライアントが接続をクローズ
    client.close(0, "client close")

    # 初回の CONNECTION_CLOSE を取り出す
    close_packet = client.send()
    assert close_packet is not None
    assert client.send() is None

    # クローズ後にパケットを受信
    result = client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
    # close() で CONNECTION_CLOSE を生成できた場合は受信処理が走り、
    # NGTCP2_ERR_CLOSING で破棄 (再アーム) になる (クラッシュしない)
    assert result == ReceiveResult.DISCARDED

    # 受信パケットへの応答として、初回と同じ CONNECTION_CLOSE が再アームされ
    # て返る (RFC 9000 Section 10.2.1 の同一パケット再送)
    retransmitted = client.send()
    assert retransmitted is not None
    assert retransmitted.data == close_packet.data


def test_send_after_close():
    """接続クローズ後は CONNECTION_CLOSE を 1 回だけ送出し、以降は None を返す"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 接続をクローズ
    client.close(0, "normal close")

    # close() 後は closed_ が true
    assert client.is_closed()

    # close() 後に send() を呼ぶと CONNECTION_CLOSE パケットを 1 回だけ返す
    result = client.send()
    assert result is not None

    # 2 回目以降の send() は None を返す
    result = client.send()
    assert result is None


def _send_with_pacing_wait(connection: Connection) -> Packet | None:
    """send() が pacing で空振りする間、期限まで待って再試行して 1 パケット返す

    直前に送信データを積んでいる前提で使う。pacing の期限は静穏判定 (1 秒)
    より長くなることがあるため打ち切りはせず、上限回数まで待つ。返るパケットは
    直前に積んだデータを含むとは限らない。
    """
    for _ in range(PUMP_ATTEMPTS):
        packet = connection.send()
        if packet is not None:
            return packet
        if not wait_pacing_timeout(connection):
            return None
    return None


def test_connection_close_retransmission_on_receive():
    """close() 後の受信パケットに応答して CONNECTION_CLOSE を再送する"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # ハンドシェイク完了イベントを消費しておく (close() 後に新たなイベントが
    # 積まれないことを検証するため)
    while client.next_event() is not None:
        pass
    while server.next_event() is not None:
        pass

    # closing 期間は close() 時に 3×PTO で固定されるため、closing 中に
    # pacing の期限待ちを挟むと満了して再送されないことがある。受信に使う
    # クライアントのパケットは close() の前に生成しておく (接続が closing で
    # ない間は pacing の期限待ちを安全に挟める)
    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, b"first", False)
    first_packet = _send_with_pacing_wait(client)
    assert first_packet is not None, "pacing 待ち後もクライアントのパケットを生成できません"
    client.send_stream_data(stream_id, b"second", False)
    second_packet = _send_with_pacing_wait(client)
    assert second_packet is not None, "pacing 待ち後もクライアントのパケットを生成できません"

    # サーバーが close() して CONNECTION_CLOSE を生成・保持する
    server.close(0x100, "server error")
    assert server.is_closed()

    # 初回の CONNECTION_CLOSE を取り出すが、ピアには渡さない (UDP ロスを再現)
    close_packet = server.send()
    assert close_packet is not None

    # 受信を挟まない 2 回目の send() は None (初回配送の契約維持)
    assert server.send() is None

    # 1 つ目の受信で再アームされ、初回と同じ CONNECTION_CLOSE が再送される
    # (RFC 9000 Section 10.2.1 の同一パケット再送)
    assert server.receive(first_packet.data, SERVER_ADDR, CLIENT_ADDR) == ReceiveResult.DISCARDED
    retransmitted = server.send()
    assert retransmitted is not None
    assert retransmitted.data == close_packet.data

    # 再送後は再び受信を挟まない限り None に戻る (受信データグラムごとに 1 回)
    assert server.send() is None

    # 2 つ目の受信でも同じパケットが再送される (再アームの繰り返し)
    assert server.receive(second_packet.data, SERVER_ADDR, CLIENT_ADDR) == ReceiveResult.DISCARDED
    retransmitted_again = server.send()
    assert retransmitted_again is not None
    assert retransmitted_again.data == close_packet.data

    # アプリが自ら close() を呼んだため、再アーム経路では終了イベントは
    # push されない (close() 自体もイベントを push しない)
    assert server.next_event() is None


def test_connection_close_retransmission_stops_after_closing_period():
    """CLOSING 期間満了後は CONNECTION_CLOSE の再送が停止する"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # ハンドシェイク完了イベントを消費しておく
    while client.next_event() is not None:
        pass
    while server.next_event() is not None:
        pass

    # closing 期間は close() 時に 3×PTO で固定されるため、closing 中に
    # pacing の期限待ちを挟むと満了して期待と食い違う。受信に使う
    # クライアントのパケットは close() の前に 4 つ生成しておく
    # (満了前の再送 2 回・満了まで保持する再アーム 1 回・満了後の受信 1 回)
    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client_packets = []
    for payload in (b"hello", b"world", b"again", b"last"):
        client.send_stream_data(stream_id, payload, False)
        packet = _send_with_pacing_wait(client)
        assert packet is not None, "pacing 待ち後もクライアントのパケットを生成できません"
        client_packets.append(packet)

    # サーバーが close() して CONNECTION_CLOSE を生成・保持する
    server.close(0x100, "server error")
    assert server.is_closed()

    # 初回配送 (close() 直後の最初の send()) を完了させる
    close_packet = server.send()
    assert close_packet is not None

    # CLOSING 期間の満了前は、受信パケットごとに従来どおり 1 回再送される
    assert (
        server.receive(client_packets[0].data, SERVER_ADDR, CLIENT_ADDR) == ReceiveResult.DISCARDED
    ), "満了前の受信が破棄になりません"
    retransmitted = server.send()
    assert retransmitted is not None
    assert retransmitted.data == close_packet.data

    # 再アームの繰り返しでも満了前は同じパケットが再送される (肯定確認)
    assert (
        server.receive(client_packets[1].data, SERVER_ADDR, CLIENT_ADDR) == ReceiveResult.DISCARDED
    ), "満了前の 2 回目の受信が破棄になりません"
    retransmitted_again = server.send()
    assert retransmitted_again is not None
    assert retransmitted_again.data == close_packet.data

    # 3 回目の受信では再アームだけして send() を呼ばず、armed のまま満了を
    # 迎えさせる (満了後も再アーム済みのパケットを返さないことを破棄に
    # 依存せず確認するため)
    assert (
        server.receive(client_packets[2].data, SERVER_ADDR, CLIENT_ADDR) == ReceiveResult.DISCARDED
    ), "満了前の 3 回目の受信が破棄になりません"

    # CLOSING 期間の満了まで実時間待ちする (get_timeout() が残り時間を返す)
    timeout = server.get_timeout()
    assert timeout is not None
    time.sleep((timeout + 50_000_000) / 1_000_000_000)

    # 満了後は get_timeout() が 0 を返し、handle_timeout() の呼び出しを促す
    assert server.get_timeout() == 0

    # 満了後、handle_timeout() を呼ぶ前 (保持パケット破棄前) でも、満了前に
    # 再アーム済みの CONNECTION_CLOSE は send() が返さない (再送停止が破棄に
    # 依存しないことを確認)
    assert server.send() is None

    # 満了後は受信パケットにも応答しない。receive() は終了を返し、再アームも
    # ConnectionClosed イベントの push も行わない
    assert (
        server.receive(client_packets[3].data, SERVER_ADDR, CLIENT_ADDR) == ReceiveResult.CLOSED
    ), "満了後の受信が終了になりません"
    assert server.send() is None
    assert server.next_event() is None

    # handle_timeout() で保持パケットが破棄され、get_timeout() が None に戻る
    server.handle_timeout()
    assert server.get_timeout() is None


def test_connection_close_first_send_after_closing_period():
    """CLOSING 期間満了後でも初回の send() は CONNECTION_CLOSE を返す"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # ハンドシェイク完了イベントを消費しておく
    while client.next_event() is not None:
        pass
    while server.next_event() is not None:
        pass

    # サーバーが close() して CONNECTION_CLOSE を生成・保持する
    # close() 前に PTO を取得する (公開アクセサの pto は closed_ 後は None)
    pto = server.pto
    assert pto is not None
    server.close(0x100, "server error")
    assert server.is_closed()

    # CLOSING 期間の満了まで実時間待ちする (初回配送を満了まで遅延)
    timeout = server.get_timeout()
    assert timeout is not None
    # 満了時刻は close() 時刻 + 3×PTO で、RFC 9000 Section 10.2 の下界
    # (at least three times the current PTO interval) を下回らない
    # (close() から get_timeout() までの経過時間ぶんだけ残り時間は減るため
    # 係数 0.9 で余裕を持って確認する)
    assert timeout >= 3 * pto * 0.9
    time.sleep((timeout + 50_000_000) / 1_000_000_000)

    # 満了後、handle_timeout() を呼んでも初回配送前のため破棄されない
    # (最初の send() が CONNECTION_CLOSE を返せる状態を保つ)
    server.handle_timeout()
    assert server.get_timeout() == 0

    # 満了後でも初回配送は CONNECTION_CLOSE を返す (満了判定の対象外)
    close_packet = server.send()
    assert close_packet is not None

    # 初回配送後は満了しているため再送しない
    assert server.send() is None

    # handle_timeout() で保持パケットが破棄され、get_timeout() が None に戻る
    server.handle_timeout()
    assert server.get_timeout() is None


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
        server.receive(packet.data, SERVER_ADDR, CLIENT_ADDR)

    # FIN 後にさらにデータを送信しようとする
    client.send_stream_data(stream_id, b"more data", False)

    # send() を呼んでもクラッシュしない
    client.send()

    # 接続は閉じられていないこと
    assert not client.is_closed()


def test_multiple_close_calls():
    """接続を複数回クローズしても CONNECTION_CLOSE は 1 回だけ送出される"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 複数回クローズを呼ぶ (2 回目以降は closed_ ガードで no-op)
    client.close(0, "first close")
    client.close(1, "second close")
    client.close(2, "third close")

    # クラッシュしないこと、closed_ が立つことを確認
    assert client.is_closed()

    # CONNECTION_CLOSE は 1 回だけ送出される (2 回目以降は None)
    result = client.send()
    assert result is not None
    assert client.send() is None


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

    # send() を呼んでもクラッシュしない。close() が生成した
    # CONNECTION_CLOSE パケットが返る
    result = client.send()
    assert result is not None


class _CustomVerifyError(Exception):
    """検証コールバック用の独自例外"""


def _pump_until_client_closed(client: Connection, server: Connection) -> None:
    """クライアントが閉じるまでパケットを交換する

    pacing 有効時は send() が期限待ちで空振りするため、両方空振りの
    場合は get_timeout() の期限まで待って再試行する
    """
    # 証明書検証の失敗でハンドシェイクが進まなくなり、クライアントが閉じる
    for _ in range(PUMP_ATTEMPTS):
        server_packet = server.send()
        if server_packet:
            client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)

        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)

        if client.is_closed():
            break

        if (
            server_packet is None
            and client_packet is None
            and not wait_pacing_timeout(client, server)
        ):
            break


def _connect_with_raising_callback(callback) -> Connection:
    """例外を送出する検証コールバックでハンドシェイクを試みる"""
    # 検証コールバックに例外を送出するクライアントを用意する
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.server_name = "localhost"
    client_config.verify_callback = callback

    # 自己署名証明書のサーバーを用意する
    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    initial_packet = client.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    server.receive(initial_packet.data, SERVER_ADDR, CLIENT_ADDR)

    # 送出してもプロセスは継続し、クライアントが閉じる
    _pump_until_client_closed(client, server)
    assert client.is_closed()
    return client


def _connection_closed_reason(client: Connection) -> str:
    """クライアントの ConnectionClosed イベントの reason を返す"""
    # ハンドシェイク失敗に伴う終了イベントを探す
    reasons = []
    while True:
        event = client.next_event()
        if event is None:
            break
        if event.type == EventType.CONNECTION_CLOSED:
            reasons.append(event.reason)
    assert len(reasons) == 1
    return reasons[0]


def test_verify_callback_value_error():
    """検証コールバックの ValueError でプロセスが継続する"""

    def verify_callback(certificates: list[bytes]) -> bool:
        raise ValueError("test-value-error")

    client = _connect_with_raising_callback(verify_callback)

    # 終了イベントの reason に例外情報が含まれる
    reason = _connection_closed_reason(client)
    assert "ValueError" in reason
    assert "test-value-error" in reason


def test_verify_callback_runtime_error():
    """検証コールバックの RuntimeError でプロセスが継続する"""

    def verify_callback(certificates: list[bytes]) -> bool:
        raise RuntimeError("test-runtime-error")

    client = _connect_with_raising_callback(verify_callback)

    # 終了イベントの reason に例外情報が含まれる
    reason = _connection_closed_reason(client)
    assert "RuntimeError" in reason
    assert "test-runtime-error" in reason


def test_verify_callback_custom_error():
    """検証コールバックの独自例外でプロセスが継続する"""

    def verify_callback(certificates: list[bytes]) -> bool:
        raise _CustomVerifyError("test-custom-error")

    client = _connect_with_raising_callback(verify_callback)

    # 終了イベントの reason に例外情報が含まれる
    reason = _connection_closed_reason(client)
    assert "_CustomVerifyError" in reason
    assert "test-custom-error" in reason


def test_verify_callback_keyboard_interrupt():
    """検証コールバックの KeyboardInterrupt は再送出される"""

    def verify_callback(certificates: list[bytes]) -> bool:
        raise KeyboardInterrupt("test-interrupt")

    # 割り込み系は検証失敗に丸めず、呼び出し元へ再送出する
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.server_name = "localhost"
    client_config.verify_callback = verify_callback

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    initial_packet = client.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    server.receive(initial_packet.data, SERVER_ADDR, CLIENT_ADDR)

    # 受信処理で KeyboardInterrupt が送出され、プロセスは継続する
    raised = False
    for _ in range(PUMP_ATTEMPTS):
        server_packet = server.send()
        if server_packet:
            try:
                client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
            except KeyboardInterrupt:
                raised = True
                break

        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)

        # pacing 期限待ちで空振りするため待って再試行する
        if (
            server_packet is None
            and client_packet is None
            and not wait_pacing_timeout(client, server)
        ):
            break

    assert raised

    # 送出は使い切りで、二重送出しない
    client.receive(b"\x00" * 100, CLIENT_ADDR, SERVER_ADDR)

    # 割り込み系は通常例外に丸めないため、終了イベントの reason に検証
    # 情報は含まれず、従来のフォールバックになる
    assert _connection_closed_reason(client) == "crypto error"


def test_verify_callback_system_exit():
    """検証コールバックの SystemExit は再送出される"""

    def verify_callback(certificates: list[bytes]) -> bool:
        raise SystemExit("test-exit")

    # 割り込み系は検証失敗に丸めず、呼び出し元へ再送出する
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.server_name = "localhost"
    client_config.verify_callback = verify_callback

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    initial_packet = client.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    server.receive(initial_packet.data, SERVER_ADDR, CLIENT_ADDR)

    # 受信処理で SystemExit が送出され、プロセスは継続する
    raised = False
    for _ in range(PUMP_ATTEMPTS):
        server_packet = server.send()
        if server_packet:
            try:
                client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
            except SystemExit:
                raised = True
                break

        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)

        # pacing 期限待ちで空振りするため待って再試行する
        if (
            server_packet is None
            and client_packet is None
            and not wait_pacing_timeout(client, server)
        ):
            break

    assert raised


def test_verify_callback_cancelled_error():
    """検証コールバックの CancelledError は再送出される"""
    import asyncio

    def verify_callback(certificates: list[bytes]) -> bool:
        raise asyncio.CancelledError("test-cancel")

    # 割り込み系は検証失敗に丸めず、呼び出し元へ再送出する
    client_config = Config()
    client_config.alpn_protocols = ["h3"]
    client_config.server_name = "localhost"
    client_config.verify_callback = verify_callback

    server_config = Config()
    server_config.cert_file = CERTFILE
    server_config.key_file = KEYFILE
    server_config.alpn_protocols = ["h3"]

    client = Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    initial_packet = client.send()
    assert initial_packet is not None
    server = Connection.accept(server_config, initial_packet.data, SERVER_ADDR, CLIENT_ADDR)
    server.receive(initial_packet.data, SERVER_ADDR, CLIENT_ADDR)

    # 受信処理で CancelledError が送出され、プロセスは継続する
    raised = False
    for _ in range(PUMP_ATTEMPTS):
        server_packet = server.send()
        if server_packet:
            try:
                client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
            except asyncio.CancelledError:
                raised = True
                break

        client_packet = client.send()
        if client_packet:
            server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)

        # pacing 期限待ちで空振りするため待って再試行する
        if (
            server_packet is None
            and client_packet is None
            and not wait_pacing_timeout(client, server)
        ):
            break

    assert raised


def test_verify_callback_long_message_truncated():
    """検証コールバックの長文メッセージは切り詰められて reason になる"""
    # 1024 バイト超の ASCII とマルチバイトのメッセージを用意する
    long_message = "x" * 2000 + "あ" * 500

    def verify_callback(certificates: list[bytes]) -> bool:
        raise ValueError(long_message)

    client = _connect_with_raising_callback(verify_callback)

    # reason は 1024 バイト以内に収まり、有効な UTF-8 である
    reason = _connection_closed_reason(client)
    assert len(reason.encode("utf-8")) <= 1024
    assert "ValueError" in reason


def test_verify_callback_truncation_boundaries():
    """切り詰め境界がマルチバイト文字に当たっても reason が有効である"""
    # reason 全体は "verify callback failed: ValueError: " (36 バイト) に続く
    # 本文のため、1024 バイト境界の位置を本文側の文字種で調整する。期待値は
    # 36 + 本文側の保持バイト数で、不完全な末尾文字は先行バイトごと除去し、
    # 完全な末尾文字は全保持する
    cases = [
        # 境界が 3 バイト文字の途中に当たる (987 バイト保持で 1023 バイト)
        ("あ" * 400, 1023),
        # 境界が 3 バイト文字の切れ目に当たる (全保持で 1024 バイト)
        ("あ" * 329 + "x" * 500, 1024),
        # 境界が 2 バイト文字の途中に当たる (987 バイト保持で 1023 バイト)
        ("x" + "é" * 600, 1023),
        # 境界が 4 バイト文字の途中に当たる (985 バイト保持で 1021 バイト)
        ("x" + "𝄞" * 300, 1021),
    ]

    for message, expected_length in cases:

        def verify_callback(certificates: list[bytes], _message: str = message) -> bool:
            raise ValueError(_message)

        client = _connect_with_raising_callback(verify_callback)

        # 不正な UTF-8 では reason 取得時に例外になるため、読めた時点で有効
        reason = _connection_closed_reason(client)
        assert len(reason.encode("utf-8")) == expected_length
        assert "ValueError" in reason


def test_duplicate_packet_discarded():
    """重複パケットの二重受信は破棄になる"""
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)

    # 正規の 1RTT パケットを用意する (中身は重複判定に無関係)
    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, b"hello", False)
    client_packet = _send_with_pacing_wait(client)
    assert client_packet is not None, "pacing 待ち後もクライアントのパケットを生成できません"

    # 初回は受理される
    assert server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR) == ReceiveResult.ACCEPTED
    before = server.pkt_recv

    # 同一パケットの二重受信は破棄され、受信カウンタは進まない
    assert server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR) == ReceiveResult.DISCARDED
    assert server.pkt_recv == before
