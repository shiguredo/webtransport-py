"""Python 境界入力のサイズ上限テスト (quic / http2 / http3)

`nb::bytes` を入力に取るバインディングは、生の入力が 1 MiB を超えると
C++ 側 (bindings/python_input.h の check_python_input_size) で
std::invalid_argument (ValueError) になる。上限判定は > のため 1 MiB
ちょうどは通る。

対象は quic 6 箇所 (receive / send_stream_data / send_datagram / accept /
Config の session_ticket setter / early_transport_params setter)、http2 3 箇所
(receive / send_data / ping)、http3 2 箇所 (receive_stream_data / send_data)。
http2 の ping は opaque_data が 8 バイト固定という別の検証を持つため、拒否側
(1 MiB 超) だけを検証する。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest
from conftest import (
    CERTFILE,
    CLIENT_ADDR,
    KEYFILE,
    SERVER_ADDR,
    _create_http2_pair,
    _drain_events,
    _establish_session,
    _h2_pump,
    create_client_server_pair,
    perform_handshake,
    wait_pacing_timeout,
)

from webtransport import http2, http3, quic

_ONE_MIB = 1024 * 1024


def _make_quic_client() -> quic.Connection:
    """入力検査を試すための QUIC クライアント接続を作成する

    上限検査はセッション状態の検査より先に走るため、ハンドシェイク前の
    接続でも検査の可否を確かめられる。
    """
    config = quic.Config()
    config.alpn_protocols = ["h3"]
    config.verify_peer = False
    config.server_name = "localhost"
    return quic.Connection.create_client(config, CLIENT_ADDR, SERVER_ADDR)


def _make_quic_server_config() -> quic.Config:
    """accept を試すためのサーバー設定を作成する"""
    config = quic.Config()
    config.cert_file = CERTFILE
    config.key_file = KEYFILE
    config.alpn_protocols = ["h3"]
    return config


def _make_http2_client() -> http2.Connection:
    """入力検査を試すための HTTP/2 クライアント接続を作成する"""
    return http2.Connection.create_client(http2.Config())


def _make_http3_client() -> http3.Connection:
    """入力検査を試すための HTTP/3 クライアント接続を作成する"""
    return http3.Connection.create_client(http3.Config())


def _reject(name: str, call: Callable[[bytes], object]) -> None:
    """1 MiB + 1 の入力が上限検査で拒否されることを表明する

    検査は C++ 側へのコピー前に走るため、入力の中身やセッション状態に
    よらず ValueError になり、メッセージに上限値と実際の値が入る。
    """
    with pytest.raises(
        ValueError,
        match=rf"{name} must be at most 1048576 bytes: got 1048577",
    ):
        call(b"x" * (_ONE_MIB + 1))


def _set_session_ticket(data: bytes) -> None:
    """session_ticket setter に data を設定する (拒否側の検証用)"""
    quic.Config().session_ticket = data


def _set_early_transport_params(data: bytes) -> None:
    """early_transport_params setter に data を設定する (拒否側の検証用)"""
    quic.Config().early_transport_params = data


def _pump_until_stream_data(
    client: quic.Connection,
    server: quic.Connection,
    expected_bytes: int,
) -> int:
    """クライアントの送信をサーバーへ届け、STREAM_DATA の受信量を返す

    QUIC レイヤーのパケット交換をテスト内で回す (モックなし)。Sans I/O では
    時間が明示的に進まないため、ACK 遅延等のタイマーは get_timeout() の期限を
    確認して handle_timeout() で処理する。期限がまだ来ていないタイマーは期限
    まで実時間を進めてから処理する (パケット交換ループの実行時間が ACK 遅延を
    下回るとタイマーが満了せず、クライアントの送信が輻輳ウィンドウで止まる)。
    受信バイト数が expected_bytes に達するか、パケット交換が空振りするまで
    繰り返す。空振りが続いても止まらない場合に備えてループ上限 (2000 回) を
    設ける。上限で打ち切られた場合は受信量が expected_bytes に達しないため、
    呼び出し側の表明で検出できる。
    """
    received = 0
    for _ in range(2000):
        sent_any = False
        while True:
            packet = client.send()
            if packet is None:
                break
            server.receive(packet.data, SERVER_ADDR, CLIENT_ADDR)
            sent_any = True
        while True:
            event = server.next_event()
            if event is None:
                break
            if event.type == quic.EventType.STREAM_DATA:
                received += len(event.data)
        while True:
            packet = server.send()
            if packet is None:
                break
            client.receive(packet.data, CLIENT_ADDR, SERVER_ADDR)
            sent_any = True
        if received >= expected_bytes:
            break
        # 空振りした場合はタイマーを進めて再試行する
        if not sent_any:
            client.handle_timeout()
            server.handle_timeout()
            if not wait_pacing_timeout(client, server):
                break
    return received


def _request_headers() -> list[tuple[str, str]]:
    """テスト用のリクエストヘッダーを返す"""
    return [
        (":method", "GET"),
        (":path", "/"),
        (":scheme", "https"),
        (":authority", "localhost"),
    ]


def _take_http2_packets(connection: http2.Connection) -> list[bytes]:
    """send() が空を返すまで送信データを取り出す (モックなし)"""
    packets = []
    while True:
        data = connection.send()
        if data is None:
            break
        packets.append(data)
    return packets


def test_quic_receive_rejects_input_over_one_mib() -> None:
    """quic.Connection.receive が 1 MiB 超で ValueError になることを確認"""
    _reject(
        "receive data",
        lambda data: _make_quic_client().receive(data, CLIENT_ADDR, SERVER_ADDR),
    )


def test_quic_send_stream_data_rejects_input_over_one_mib() -> None:
    """quic.Connection.send_stream_data が 1 MiB 超で ValueError になることを確認"""
    _reject(
        "send_stream_data data",
        lambda data: _make_quic_client().send_stream_data(0, data, False),
    )


def test_quic_send_datagram_rejects_input_over_one_mib() -> None:
    """quic.Connection.send_datagram が 1 MiB 超で ValueError になることを確認"""
    _reject("send_datagram data", lambda data: _make_quic_client().send_datagram(data))


def test_quic_accept_rejects_input_over_one_mib() -> None:
    """quic.Connection.accept が 1 MiB 超の initial_packet で ValueError になることを確認"""
    _reject(
        "accept initial_packet",
        lambda data: quic.Connection.accept(
            _make_quic_server_config(), data, SERVER_ADDR, CLIENT_ADDR
        ),
    )


def test_quic_session_ticket_rejects_input_over_one_mib() -> None:
    """quic.Config.session_ticket setter が 1 MiB 超で ValueError になることを確認"""
    _reject("session_ticket value", _set_session_ticket)


def test_quic_early_transport_params_rejects_input_over_one_mib() -> None:
    """quic.Config.early_transport_params setter が 1 MiB 超で ValueError になることを確認"""
    _reject("early_transport_params value", _set_early_transport_params)


def test_http2_receive_rejects_input_over_one_mib() -> None:
    """http2.Connection.receive が 1 MiB 超で ValueError になることを確認"""
    _reject("receive data", lambda data: _make_http2_client().receive(data))


def test_http2_send_data_rejects_input_over_one_mib() -> None:
    """http2.Connection.send_data が 1 MiB 超で ValueError になることを確認"""
    _reject(
        "send_data data",
        lambda data: _make_http2_client().send_data(0, data, False),
    )


def test_http2_ping_rejects_input_over_one_mib() -> None:
    """http2.Connection.ping が 1 MiB 超で ValueError になることを確認

    ping は 8 バイト固定の検証を持つため、1 MiB 超が上限検査で先に拒否される
    ことだけを固定する (1 MiB ちょうどの受理は対象外)。
    """
    _reject("ping opaque_data", lambda data: _make_http2_client().ping(data))


def test_http3_receive_stream_data_rejects_input_over_one_mib() -> None:
    """http3.Connection.receive_stream_data が 1 MiB 超で ValueError になることを確認"""
    _reject(
        "receive_stream_data data",
        lambda data: _make_http3_client().receive_stream_data(0, data, False),
    )


def test_http3_send_data_rejects_input_over_one_mib() -> None:
    """http3.Connection.send_data が 1 MiB 超で ValueError になることを確認"""
    _reject(
        "send_data data",
        lambda data: _make_http3_client().send_data(0, data, False),
    )


def test_quic_input_check_precedes_state_guard() -> None:
    """終了済み接続や DATAGRAM 無効時でも入力検査が先に走ることを確認

    binding 層の入力検査は `conn_` / `closed_` / `enable_datagram` による
    早期 return より前に実行されるため、送信が黙って無視される状態でも
    1 MiB 超は ValueError になる。検査を状態ガードの後ろへ移す退行を検出する。
    """
    closed = _make_quic_client()
    closed.close()
    with pytest.raises(ValueError, match=r"send_stream_data data must be"):
        closed.send_stream_data(0, b"x" * (_ONE_MIB + 1), False)
    with pytest.raises(ValueError, match=r"send_datagram data must be"):
        closed.send_datagram(b"x" * (_ONE_MIB + 1))

    # DATAGRAM を無効化した接続は send_datagram が早期 return する
    datagram_disabled_config = quic.Config()
    datagram_disabled_config.enable_datagram = False
    datagram_disabled = quic.Connection.create_client(
        datagram_disabled_config, CLIENT_ADDR, SERVER_ADDR
    )
    with pytest.raises(ValueError, match=r"send_datagram data must be"):
        datagram_disabled.send_datagram(b"x" * (_ONE_MIB + 1))


def test_quic_client_register_early_data_rejects_input_over_one_mib() -> None:
    """quic.Client.register_early_data が 1 MiB 超を登録時に拒否することを確認

    送出は connect() 中の _flush_early_data で行われるため、登録時に検査しないと
    接続確立処理の途中で未文書の ValueError になる。登録時点で拒否されることを
    固定する。
    """
    from webtransport.quic import Client

    client = Client(host="127.0.0.1", port=4433)

    with pytest.raises(ValueError, match=r"early data must be at most 1048576 bytes"):
        client.register_early_data(b"x" * (_ONE_MIB + 1), fin=True)

    # 1 MiB ちょうどは登録できる
    client.register_early_data(b"x" * _ONE_MIB, fin=True)


def test_quic_receive_accepts_one_mib() -> None:
    """quic.Connection.receive が 1 MiB ちょうどを受理することを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。ハンドシェイク前の接続では所属不明のパケットとして破棄されるが、
    入力上限の検査は通過する。
    """
    client = _make_quic_client()

    assert client.receive(b"x" * _ONE_MIB, CLIENT_ADDR, SERVER_ADDR) == quic.ReceiveResult.DISCARDED


def test_quic_send_stream_data_accepts_one_mib() -> None:
    """quic.Connection.send_stream_data が 1 MiB ちょうどを受理することを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。ハンドシェイク後の接続で 1 MiB を送信し、ピアが全量を受信する
    ところまで表明する。
    """
    client, server, initial_packet = create_client_server_pair()
    assert perform_handshake(client, server, initial_packet)
    stream_id = client.open_stream(True)
    assert stream_id >= 0

    client.send_stream_data(stream_id, b"x" * _ONE_MIB, False)

    assert _pump_until_stream_data(client, server, _ONE_MIB) == _ONE_MIB


def test_quic_send_datagram_accepts_one_mib() -> None:
    """quic.Connection.send_datagram が 1 MiB ちょうどを受理することを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。ピア広告値を超えるデータグラムは送信時に破棄され、低レベル API に
    送信キューの長さを観測する手段が無いため、ハンドシェイク前の接続で
    例外が出ないことだけを表明する。
    """
    client = _make_quic_client()

    client.send_datagram(b"x" * _ONE_MIB)


def test_quic_accept_accepts_one_mib() -> None:
    """quic.Connection.accept が 1 MiB ちょうどの initial_packet で接続を作れることを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。accept は入力の中身を QUIC として解釈するため、有効な Initial
    パケットを 1 MiB まで伸ばした入力を使い、接続が得られることまで表明する。
    """
    client = _make_quic_client()
    initial_packet = client.send()
    assert initial_packet is not None
    # 有効な Initial パケットを上限まで伸ばす
    padded = initial_packet.data + b"\x00" * (_ONE_MIB - len(initial_packet.data))

    server = quic.Connection.accept(_make_quic_server_config(), padded, SERVER_ADDR, CLIENT_ADDR)
    assert server is not None


def test_quic_session_ticket_accepts_one_mib() -> None:
    """quic.Config.session_ticket setter が 1 MiB ちょうどを受理することを確認"""
    config = quic.Config()

    config.session_ticket = b"x" * _ONE_MIB
    assert len(config.session_ticket) == _ONE_MIB


def test_quic_early_transport_params_accepts_one_mib() -> None:
    """quic.Config.early_transport_params setter が 1 MiB ちょうどを受理することを確認"""
    config = quic.Config()

    config.early_transport_params = b"x" * _ONE_MIB
    assert len(config.early_transport_params) == _ONE_MIB


def test_http2_receive_accepts_one_mib() -> None:
    """http2.Connection.receive が 1 MiB ちょうどを受理することを確認

    戻り値は nghttp2 が消費したバイト数になるため、全量が渡ったことまで表明する。
    """
    client = _make_http2_client()

    assert client.receive(b"x" * _ONE_MIB) == _ONE_MIB


def test_http2_send_data_accepts_one_mib() -> None:
    """http2.Connection.send_data が 1 MiB ちょうどを受理することを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。1 MiB は初期ウィンドウ (65535) を超えるため、パケットを往復させて
    フロー制御の更新を返しながら送出し、送信データが得られることまで表明する。
    """
    client, server = _create_http2_pair()
    stream_id = client.submit_request(_request_headers())
    assert stream_id > 0
    _h2_pump(client, server)
    _drain_events(server)

    client.send_data(stream_id, b"x" * _ONE_MIB, True)

    sent_bytes = 0
    for _ in range(2000):
        packets = _take_http2_packets(client)
        for packet in packets:
            server.receive(packet)
            sent_bytes += len(packet)
        # サーバーの応答 (WINDOW_UPDATE / ACK) をクライアントへ返す
        for packet in _take_http2_packets(server):
            client.receive(packet)
        if sent_bytes >= _ONE_MIB:
            break

    # 1 MiB を初期ウィンドウ (65535) 超のフロー制御を挟みつつ送出できたことを
    # 表明する (ループの打ち切りと実装不具合を区別できる粒度にする)
    assert sent_bytes >= _ONE_MIB


def test_http2_request_rejects_body_over_one_mib() -> None:
    """http2.Client.request が 1 MiB 超の body で送信前に ValueError になることを確認

    request はヘッダー送出後にボディを送るため、ボディの検査が後れると
    ストリームが未終端で残る。接続前でも送信前に拒否されることを固定する。
    """
    from webtransport.http2 import Client

    client = Client(host="127.0.0.1", port=0)

    with pytest.raises(ValueError, match=r"request body must be at most 1048576 bytes"):
        asyncio.run(client.request("POST", "/", body=b"x" * (_ONE_MIB + 1)))


def test_http2_ping_accepts_eight_bytes() -> None:
    """http2.Connection.ping が 8 バイトの入力を上限検査で拒否しないことを確認

    上限検査の追加で 8 バイトの正規入力が妨げられていないことを固定する
    (8 バイト固定の検証そのものは既存テストが担当する)。
    """
    client = _make_http2_client()

    client.ping(b"x" * 8)


def test_http3_receive_stream_data_accepts_one_mib() -> None:
    """http3.Connection.receive_stream_data が 1 MiB ちょうどを受理することを確認"""
    client = _make_http3_client()

    assert client.receive_stream_data(0, b"x" * _ONE_MIB, False) == 0


def test_http3_send_data_accepts_one_mib() -> None:
    """http3.Connection.send_data が 1 MiB ちょうどを受理することを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。制御ストリームを結線しリクエストを登録した接続で送信し、送信
    キューに 1 MiB の DATA フレームが積まれることまで表明する。
    """
    client = _make_http3_client()
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    assert client.submit_request(0, _request_headers()) is True

    client.send_data(0, b"x" * _ONE_MIB, True)

    streams = client.get_streams_to_send()
    # 1 MiB のペイロードが送信待ちに積まれる (HEADERS とフレームヘッダの分を
    # 含めると 1 MiB を超える)。バッファの分割のされ方には依存しない
    assert sum(len(data) for _stream_id, data, _fin in streams) > _ONE_MIB


@pytest.mark.asyncio
async def test_http3_client_send_data_of_one_mib_reaches_server(test_certificates) -> None:
    """http3.Client が 1 MiB ちょうどを送信してサーバーが全量を受信することを確認

    上位層が組み立てた HTTP/3 のフレームは QUIC 層へ渡す時点でフレームヘッダの
    分だけ 1 MiB を超える。QUIC 層の入力サイズ検査がこの内部経路に掛かると
    送信できないため、上限ちょうどの入力が相手まで届くことを固定する。
    """
    import asyncio

    from webtransport.http3 import Client, Server

    payload = b"x" * _ONE_MIB
    received = 0
    received_event = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        # 応答を返すだけで本文は受信しない (受信は on_data が担う)
        await server.submit_response(
            addr, stream_id, [(":status", "200"), ("content-type", "text/plain")]
        )

    async def on_server_data(stream_id, data, addr):
        nonlocal received
        received += len(data)
        if received >= len(payload):
            received_event.set()

    server.on_request(on_request)
    server.on_data(on_server_data)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    await client.connect()

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    try:
        stream_id = await client.request("POST", "/")
        assert stream_id >= 0
        await client.send_data(stream_id, payload, fin=True)

        await asyncio.wait_for(received_event.wait(), timeout=10.0)
        assert received == len(payload)
    finally:
        client_task.cancel()
        server_task.cancel()
        await asyncio.gather(client_task, server_task, return_exceptions=True)

        await client.close()
        await server.stop()


@pytest.mark.asyncio
async def test_http3_server_send_data_of_one_mib_reaches_client(test_certificates) -> None:
    """http3.Server が 1 MiB ちょうどを送信してクライアントが全量を受信することを確認"""
    import asyncio

    from webtransport.http3 import Client, Server

    payload = b"x" * _ONE_MIB
    received = 0
    received_event = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_request(stream_id, headers, addr):
        await server.submit_response(
            addr, stream_id, [(":status", "200"), ("content-type", "text/plain")]
        )
        await server.send_data(addr, stream_id, payload, fin=True)

    server.on_request(on_request)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)

    async def on_client_data(stream_id, data):
        nonlocal received
        received += len(data)
        if received >= len(payload):
            received_event.set()

    client.on_data(on_client_data)
    await client.connect()

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    try:
        stream_id = await client.request("GET", "/")
        assert stream_id >= 0

        await asyncio.wait_for(received_event.wait(), timeout=10.0)
        assert received == len(payload)
    finally:
        client_task.cancel()
        server_task.cancel()
        await asyncio.gather(client_task, server_task, return_exceptions=True)

        await client.close()
        await server.stop()


def test_h3_send_datagram_of_one_mib_is_accepted() -> None:
    """h3.Session が 1 MiB ちょうどのデータグラムを受理し、ワイヤ形式を返すことを確認

    データグラムは Quarter Stream ID が前置されるため、QUIC 層へ渡す時点で
    1 MiB を超える。1 MiB のデータグラムは 1 パケットに収まらず実網では
    送れないため、ここでは h3 セッション層の入力上限の境界だけを固定する
    (リレー経路そのものは実ソケットのストリーム送信テストが守る)。
    """
    client, _server, session_id = _establish_session()

    client.send_datagram(session_id, b"x" * _ONE_MIB)

    datagrams = client.get_datagrams_to_send()
    assert len(datagrams) == 1
    # Quarter Stream ID (1 バイトの varint) が前置されるため 1 MiB + 1 になる
    assert len(datagrams[0]) == _ONE_MIB + 1


@pytest.mark.asyncio
async def test_h3_client_send_stream_data_of_one_mib_reaches_server(test_certificates) -> None:
    """h3.Client が 1 MiB ちょうどのストリームデータを送信してサーバーが受信することを確認

    h3 のストリームデータは WT ストリームヘッダやフレームヘッダが付くため、
    QUIC 層へ渡す時点で 1 MiB を超える。QUIC 層の入力サイズ検査がこの内部
    リレー経路に掛かると上位層の送信が壊れるため、実ソケット越しに上限
    ちょうどの入力が相手まで届くことを固定する。
    """
    import asyncio

    from webtransport.h3 import Client, Server

    payload = b"x" * _ONE_MIB
    received = 0
    received_event = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_session_ready(session_id, addr):
        pass

    async def on_stream_data(session_id, stream_id, data, addr):
        nonlocal received
        received += len(data)
        if received >= len(payload):
            received_event.set()

    server.on_session_ready(on_session_ready)
    server.on_stream_data(on_stream_data)

    await server.start()

    async def run_server():
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        url=f"https://127.0.0.1:{server.actual_port}/webtransport",
        verify_peer=False,
    )
    await client.connect()

    async def run_client():
        try:
            await client.run()
        except asyncio.CancelledError:
            pass

    client_task = asyncio.create_task(run_client())

    try:
        stream_id = await client.open_stream()
        assert stream_id >= 0
        await client.send_stream_data(stream_id, payload)

        await asyncio.wait_for(received_event.wait(), timeout=10.0)
        assert received == len(payload)
    finally:
        client_task.cancel()
        server_task.cancel()
        await asyncio.gather(client_task, server_task, return_exceptions=True)

        await client.close()
        await server.stop()
