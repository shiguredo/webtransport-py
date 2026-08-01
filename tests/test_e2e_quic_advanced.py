"""QUIC 証明書検証 / 0-RTT / Connection Migration の e2e テスト"""

from __future__ import annotations

import asyncio
import logging

import pytest

from webtransport.quic import Client, Server

logger = logging.getLogger(__name__)


def _parse_der_tlv(data: bytes, start: int) -> tuple[int, int, int]:
    """DER 要素を解析して (タグ、内容開始位置、内容長) を返す

    Args:
        data: DER データ
        start: 要素のタグ位置

    Returns:
        (タグ、内容開始位置、内容長)
    """
    tag = data[start]
    length = data[start + 1]
    content_start = start + 2
    if length & 0x80:
        # long form: 長さを表すバイト数が続く
        num_bytes = length & 0x7F
        content_length = int.from_bytes(data[content_start : content_start + num_bytes], "big")
        content_start += num_bytes
    else:
        content_length = length
    return tag, content_start, content_length


def _find_ticket_element(session_der: bytes) -> tuple[int, int]:
    """SSL_SESSION の DER から ticket 要素 ([10]) の内容位置と内容長を探す

    BoringSSL の SSL_SESSION は SEQUENCE 直下に TLV を並べた構造で、
    ticket は context-specific 構造型のタグ 10 (0xAA) として現れる。

    Returns:
        (ticket 内容の開始位置、内容の長さ)
    """
    pos = 0
    tag, content_start, content_length = _parse_der_tlv(session_der, pos)
    assert tag == 0x30, "SSL_SESSION は SEQUENCE で始まるべき"
    end = content_start + content_length
    pos = content_start
    while pos < end:
        tag, content_start, content_length = _parse_der_tlv(session_der, pos)
        if tag == 0xAA:
            return content_start, content_length
        pos = content_start + content_length
    raise AssertionError("ticket ([10]) 要素が見つからない")


def _corrupt_session_ticket(ticket: bytes) -> bytes:
    """セッションチケット (DER) の ticket ペイロード末尾 1 バイトを反転する

    サーバーが復号・検証する暗号化ペイロードの末尾を破壊するため、
    サーバー側の検証は必ず失敗して 0-RTT が拒否される。ticket は
    opaque データのため DER 構造は保たれ、クライアント側のチケット復元
    (d2i_SSL_SESSION) は成功する。
    """
    content_start, content_length = _find_ticket_element(ticket)
    corrupted = bytearray(ticket)
    corrupted[content_start + content_length - 1] ^= 0xFF
    return bytes(corrupted)


async def _await_session_ticket(client: Client) -> tuple[bytes, bytes]:
    """接続済みクライアントのセッションチケットと 0-RTT トランスポートパラメータを待って返す

    NewSessionTicket の到着をポーリングで待つ。

    Args:
        client: 接続済みのクライアント

    Returns:
        (セッションチケット、0-RTT トランスポートパラメータ)
    """
    for _ in range(50):
        ticket = client.export_session_ticket()
        if ticket:
            break
        await asyncio.sleep(0.05)

    assert ticket, "セッションチケットを取得できるべき"
    early_tp = client.export_0rtt_transport_params()
    assert early_tp, "0-RTT トランスポートパラメータを取得できるべき"
    return ticket, early_tp


@pytest.mark.asyncio
async def test_verify_peer_rejects_self_signed(test_certificates):
    """自己署名証明書を verify_peer=True で拒否することを確認する"""

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    # CA なし + 検証有効では自己署名を信頼できない
    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=True,
    )
    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is False, "自己署名証明書は検証失敗になるべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_verify_peer_with_ca_file(test_certificates):
    """ca_file にサーバー証明書を渡せば verify_peer=True で接続できることを確認する"""

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    # 自己署名を CA として明示指定する
    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=True,
        ca_file=test_certificates["certfile"],
    )
    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is True, "ca_file 指定時は検証に成功するべき"

    client_task = asyncio.create_task(client.run())
    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_custom_verify_callback_accept(test_certificates):
    """カスタム検証コールバックで許可できることを確認する"""

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    seen_certs: list[list[bytes]] = []

    def verify_callback(certs: list[bytes]) -> bool:
        # ピア証明書チェーンを受け取り、内容を記録して許可する
        seen_certs.append(certs)
        logger.info("カスタム検証: 証明書 %d 枚を許可する", len(certs))
        return True

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=True,
        verify_callback=verify_callback,
    )
    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is True, "カスタム検証で許可すれば接続できるべき"
    assert len(seen_certs) >= 1
    assert len(seen_certs[0]) >= 1
    assert len(seen_certs[0][0]) > 0

    client_task = asyncio.create_task(client.run())
    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_custom_verify_callback_reject(test_certificates):
    """カスタム検証コールバックで拒否できることを確認する"""

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    def verify_callback(certs: list[bytes]) -> bool:
        logger.info("カスタム検証: 証明書を拒否する")
        return False

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=True,
        verify_callback=verify_callback,
    )
    connected = await asyncio.wait_for(client.connect(), timeout=5.0)
    assert connected is False, "カスタム検証で拒否すれば接続できないべき"

    server_task.cancel()
    await asyncio.gather(server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_session_ticket_and_0rtt(test_certificates):
    """初回接続で ticket を取得し、再接続で 0-RTT を試行できることを確認する"""

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    port = server.actual_port

    # 1 回目: フルハンドシェイクで ticket と 0-RTT TP を得る
    client1 = Client(host="127.0.0.1", port=port, verify_peer=False)
    tickets: list[bytes] = []

    async def on_ticket(ticket: bytes) -> None:
        tickets.append(ticket)
        logger.info("セッションチケットを受信: %d バイト", len(ticket))

    client1.on_session_ticket(on_ticket)
    assert await asyncio.wait_for(client1.connect(), timeout=5.0) is True

    client1_task = asyncio.create_task(client1.run())
    ticket, early_tp = await _await_session_ticket(client1)

    client1_task.cancel()
    await asyncio.gather(client1_task, return_exceptions=True)
    await client1.close()

    # 2 回目: ticket + TP で early data を試行する
    client2 = Client(
        host="127.0.0.1",
        port=port,
        verify_peer=False,
        session_ticket=ticket,
        early_transport_params=early_tp,
        enable_early_data=True,
    )
    assert await asyncio.wait_for(client2.connect(), timeout=5.0) is True
    assert client2.was_early_data_attempted() is True, "0-RTT を試行しているべき"
    logger.info(
        "0-RTT 受理=%s 試行=%s",
        client2.is_early_data_accepted(),
        client2.was_early_data_attempted(),
    )

    # 受理されなくても試行できていれば完了条件を満たす
    # (サーバ設定やタイミングで拒否される場合がある)
    client2_task = asyncio.create_task(client2.run())
    client2_task.cancel()
    server_task.cancel()
    await asyncio.gather(client2_task, server_task, return_exceptions=True)
    await client2.close()
    await server.stop()


@pytest.mark.asyncio
async def test_early_data_send_receive(test_certificates):
    """0-RTT early data をハンドシェイク完了前にサーバーへ届け、応答を受け取れることを確認する

    同一サーバープロセスへ 2 回接続し、1 回目で得た ticket と 0-RTT
    トランスポートパラメータを使って 2 回目に early data を送信する。
    サーバー側は自身のハンドシェイク完了前に early data を受信し、
    ハンドシェイク完了後にエコーバックする。
    """

    server_received: list[bytes] = []
    server_received_before_handshake: list[bool] = []
    server_handshake_done: set[tuple[str, int]] = set()
    # ハンドシェイク完了前に届いた early data は、完了後にエコーバックする
    pending_echo: dict[tuple[str, int], list[tuple[int, bytes, bool]]] = {}
    client_received: list[bytes] = []
    client_got_echo = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_handshake(addr: tuple[str, int]) -> None:
        # ハンドシェイク完了時点を記録する
        server_handshake_done.add(addr)
        # ハンドシェイク完了前に受信した early data をエコーバックする
        for stream_id, data, fin in pending_echo.pop(addr, []):
            await server.send_stream_data(addr, stream_id, data, fin)

    async def on_server_stream(
        stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]
    ) -> None:
        server_received.append(data)
        # early data はサーバー自身のハンドシェイク完了前に届くはず
        server_received_before_handshake.append(addr not in server_handshake_done)
        logger.info(
            "サーバー受信: %s (ハンドシェイク完了前 = %s)", data, addr not in server_handshake_done
        )
        if addr not in server_handshake_done:
            pending_echo.setdefault(addr, []).append((stream_id, data, fin))
        else:
            await server.send_stream_data(addr, stream_id, data, fin)

    server.on_handshake_completed(on_server_handshake)
    server.on_stream_data(on_server_stream)
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    port = server.actual_port

    # 1 回目: フルハンドシェイクで ticket と 0-RTT トランスポートパラメータを得る
    client1 = Client(host="127.0.0.1", port=port, verify_peer=False)
    assert await asyncio.wait_for(client1.connect(), timeout=5.0) is True

    client1_task = asyncio.create_task(client1.run())
    ticket, early_tp = await _await_session_ticket(client1)

    client1_task.cancel()
    await asyncio.gather(client1_task, return_exceptions=True)
    await client1.close()

    # 2 回目: connect() の前に early data を登録し、0-RTT として送信する
    client2 = Client(
        host="127.0.0.1",
        port=port,
        verify_peer=False,
        session_ticket=ticket,
        early_transport_params=early_tp,
        enable_early_data=True,
    )

    async def on_client_stream(stream_id: int, data: bytes, fin: bool) -> None:
        client_received.append(data)
        logger.info("クライアント受信: %s", data)
        if len(client_received) >= 2:
            client_got_echo.set()

    client2.on_stream_data(on_client_stream)
    # 登録ごとに双方向ストリームを 1 本開いて送信される
    client2.register_early_data(b"early-1", fin=False)
    client2.register_early_data(b"early-2", fin=True)

    assert await asyncio.wait_for(client2.connect(), timeout=5.0) is True
    assert client2.was_early_data_attempted() is True, "0-RTT を試行しているべき"
    assert client2.is_early_data_accepted() is True, "0-RTT は受理されるべき"

    client2_task = asyncio.create_task(client2.run())
    await asyncio.wait_for(client_got_echo.wait(), timeout=5.0)

    assert server_received == [b"early-1", b"early-2"], "early data は登録順に届くべき"
    assert server_received_before_handshake == [True, True], (
        "サーバーはハンドシェイク完了前に受信するべき"
    )
    assert client_received == [b"early-1", b"early-2"], "エコーバックを登録順に受信するべき"

    client2_task.cancel()
    server_task.cancel()
    await asyncio.gather(client2_task, server_task, return_exceptions=True)
    await client2.close()
    await server.stop()


@pytest.mark.asyncio
async def test_early_data_rejected(test_certificates):
    """ticket のペイロードを破損した再接続で 0-RTT が拒否されることを確認する

    1 回目で得た ticket のペイロード末尾を反転し、同一サーバーへの再接続で
    early data を試行する。サーバー側の復号・検証が失敗するため 0-RTT は
    拒否され、EARLY_DATA_REJECTED イベントが観測できる。拒否された early data
    はサーバーに届かず、コールバック内で開き直したストリームからの再送
    データだけが届く (RFC 9001 Section 4.6.2 の再送パス)。
    """

    server_received: list[bytes] = []
    server_handshake_done: set[tuple[str, int]] = set()
    server_got_retransmitted = asyncio.Event()
    retransmitted_after_handshake = False
    early_data_rejected = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_handshake(addr: tuple[str, int]) -> None:
        # ハンドシェイク完了時点を記録する
        server_handshake_done.add(addr)

    async def on_server_stream(
        stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]
    ) -> None:
        nonlocal retransmitted_after_handshake
        server_received.append(data)
        if data == b"retransmitted":
            # 再送はサーバーのハンドシェイク完了後に 1-RTT で届くはず
            retransmitted_after_handshake = addr in server_handshake_done
            server_got_retransmitted.set()

    server.on_handshake_completed(on_server_handshake)
    server.on_stream_data(on_server_stream)
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    port = server.actual_port

    # 1 回目: 正常な ticket を得る
    client1 = Client(host="127.0.0.1", port=port, verify_peer=False)
    assert await asyncio.wait_for(client1.connect(), timeout=5.0) is True

    client1_task = asyncio.create_task(client1.run())
    ticket, early_tp = await _await_session_ticket(client1)

    client1_task.cancel()
    await asyncio.gather(client1_task, return_exceptions=True)
    await client1.close()

    # 2 回目: 破損した ticket で early data を試行する
    corrupted_ticket = _corrupt_session_ticket(ticket)
    client2 = Client(
        host="127.0.0.1",
        port=port,
        verify_peer=False,
        session_ticket=corrupted_ticket,
        early_transport_params=early_tp,
        enable_early_data=True,
    )

    async def on_early_data_rejected() -> None:
        logger.info("0-RTT early data が拒否された")
        early_data_rejected.set()
        # 拒否後はストリームを開き直して再送する (1-RTT で送出される)
        retransmitted_stream = await client2.open_stream(bidirectional=True)
        await client2.send_stream_data(
            retransmitted_stream,
            b"retransmitted",
            fin=True,
        )

    client2.on_early_data_rejected(on_early_data_rejected)
    client2.register_early_data(b"should-be-rejected", fin=True)

    assert await asyncio.wait_for(client2.connect(), timeout=5.0) is True
    assert client2.was_early_data_attempted() is True, "0-RTT を試行しているべき"
    assert client2.is_early_data_accepted() is False, "破損 ticket では受理されないべき"

    client2_task = asyncio.create_task(client2.run())
    await asyncio.wait_for(early_data_rejected.wait(), timeout=5.0)
    # 再送データはサーバーに届く (拒否された early data は届かない)
    await asyncio.wait_for(server_got_retransmitted.wait(), timeout=5.0)
    assert retransmitted_after_handshake is True, "再送はハンドシェイク完了後に届くべき"
    assert server_received == [b"retransmitted"], "拒否された early data は届かず再送のみ届くべき"

    client2_task.cancel()
    server_task.cancel()
    await asyncio.gather(client2_task, server_task, return_exceptions=True)
    await client2.close()
    await server.stop()


@pytest.mark.asyncio
async def test_early_data_not_sent_without_session_ticket(test_certificates, caplog):
    """session_ticket 未指定の接続では early data が送出されないことを確認する

    0-RTT を試行しない接続では register_early_data で登録したデータは
    ストリームを開けずに破棄される。接続自体は通常どおり通信でき、
    通常のストリーム送信は届くが、登録済みの early data は届かない。
    """

    server_received: list[bytes] = []

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream(
        stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]
    ) -> None:
        server_received.append(data)

    server.on_stream_data(on_server_stream)
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    # 0-RTT を試行しない (session_ticket 未指定) 接続に early data を登録する
    client = Client(host="127.0.0.1", port=server.actual_port, verify_peer=False)
    client.register_early_data(b"never-sent", fin=True)
    with caplog.at_level(logging.WARNING, logger="webtransport.quic.client"):
        assert await asyncio.wait_for(client.connect(), timeout=5.0) is True
    assert client.was_early_data_attempted() is False, "ticket 未指定では 0-RTT を試行しないべき"

    # connect() 後の登録はエラーになる
    with pytest.raises(RuntimeError):
        client.register_early_data(b"too-late", fin=True)

    # 破棄した early data の警告が出力される
    assert any("early data was not sent" in record.message for record in caplog.records), (
        "破棄時に警告ログが出るべき"
    )

    # 接続自体は正常に通信できることを確認する
    client_task = asyncio.create_task(client.run())
    stream_id = await client.open_stream(bidirectional=True)
    await client.send_stream_data(stream_id, b"normal-data", fin=True)

    for _ in range(50):
        if b"normal-data" in server_received:
            break
        await asyncio.sleep(0.05)
    assert b"normal-data" in server_received, "通常のストリーム送信は届くべき"

    # 登録済みの early data が送られていないことを確認する
    # (誤送信されるなら通常データより先に届くため、通常データ到着後で検出できる)
    assert b"never-sent" not in server_received, "early data は送出されないべき"

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    await client.close()
    await server.stop()


@pytest.mark.asyncio
async def test_early_data_not_attempted_without_transport_params(test_certificates):
    """0-RTT トランスポートパラメータ未指定の再接続では 0-RTT を試行しないことを確認する

    セッションチケットがあっても 0-RTT トランスポートパラメータを記憶して
    いない接続では 0-RTT を試行しない (RFC 9000 Section 7.4.1)。登録済みの
    early data は送出されず、通常の通信はできる。
    """
    server_received: list[bytes] = []

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream(
        stream_id: int, data: bytes, fin: bool, addr: tuple[str, int]
    ) -> None:
        server_received.append(data)

    server.on_stream_data(on_server_stream)
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())
    port = server.actual_port

    # 1 回目: ticket を得る (0-RTT トランスポートパラメータは使わない)
    client1 = Client(host="127.0.0.1", port=port, verify_peer=False)
    assert await asyncio.wait_for(client1.connect(), timeout=5.0) is True

    client1_task = asyncio.create_task(client1.run())
    ticket, _ = await _await_session_ticket(client1)

    client1_task.cancel()
    await asyncio.gather(client1_task, return_exceptions=True)
    await client1.close()

    # 2 回目: ticket のみ指定 (0-RTT トランスポートパラメータなし) で接続する
    client2 = Client(
        host="127.0.0.1",
        port=port,
        verify_peer=False,
        session_ticket=ticket,
        enable_early_data=True,
    )
    client2.register_early_data(b"never-sent", fin=True)
    assert await asyncio.wait_for(client2.connect(), timeout=5.0) is True
    assert client2.was_early_data_attempted() is False, "TP 未指定では 0-RTT を試行しないべき"

    # 接続自体は正常に通信できることを確認する
    client2_task = asyncio.create_task(client2.run())
    stream_id = await client2.open_stream(bidirectional=True)
    await client2.send_stream_data(stream_id, b"normal-data", fin=True)

    for _ in range(50):
        if b"normal-data" in server_received:
            break
        await asyncio.sleep(0.05)
    assert b"normal-data" in server_received, "通常のストリーム送信は届くべき"

    # 登録済みの early data が送られていないことを確認する
    assert b"never-sent" not in server_received, "early data は送出されないべき"

    client2_task.cancel()
    server_task.cancel()
    await asyncio.gather(client2_task, server_task, return_exceptions=True)
    await client2.close()
    await server.stop()


@pytest.mark.asyncio
async def test_connection_migration(test_certificates):
    """initiate_migration 後もストリーム通信が継続することを確認する"""

    server_received: list[bytes] = []
    client_received: list[bytes] = []
    server_got_first = asyncio.Event()
    client_got_reply = asyncio.Event()
    server_got_second = asyncio.Event()
    client_got_second_reply = asyncio.Event()

    server = Server(
        host="127.0.0.1",
        port=0,
        certfile=test_certificates["certfile"],
        keyfile=test_certificates["keyfile"],
    )

    async def on_server_stream(stream_id: int, data: bytes, fin: bool, addr: object) -> None:
        server_received.append(data)
        logger.info("サーバー受信: %s", data)
        if data == b"before-migrate":
            server_got_first.set()
            await server.send_stream_data(addr, stream_id, b"ack-before", fin=False)
        elif data == b"after-migrate":
            server_got_second.set()
            await server.send_stream_data(addr, stream_id, b"ack-after", fin=True)

    server.on_stream_data(on_server_stream)
    await server.start()

    async def run_server() -> None:
        try:
            await server.run()
        except asyncio.CancelledError:
            pass

    server_task = asyncio.create_task(run_server())

    client = Client(
        host="127.0.0.1",
        port=server.actual_port,
        verify_peer=False,
    )

    async def on_client_stream(stream_id: int, data: bytes, fin: bool) -> None:
        client_received.append(data)
        logger.info("クライアント受信: %s", data)
        if data == b"ack-before":
            client_got_reply.set()
        elif data == b"ack-after":
            client_got_second_reply.set()

    client.on_stream_data(on_client_stream)
    assert await asyncio.wait_for(client.connect(), timeout=5.0) is True

    client_task = asyncio.create_task(client.run())
    stream_id = await client.open_stream()
    await client.send_stream_data(stream_id, b"before-migrate", fin=False)

    await asyncio.wait_for(server_got_first.wait(), timeout=5.0)
    await asyncio.wait_for(client_got_reply.wait(), timeout=5.0)

    # ローカルアドレスを変えてマイグレーションする
    migrated = await client.migrate()
    assert migrated is True, "マイグレーションを開始できるべき"
    logger.info("マイグレーションを開始した")

    # path validation と通信継続を待つ
    await asyncio.sleep(0.2)
    await client.send_stream_data(stream_id, b"after-migrate", fin=True)

    await asyncio.wait_for(server_got_second.wait(), timeout=5.0)
    await asyncio.wait_for(client_got_second_reply.wait(), timeout=5.0)

    assert b"before-migrate" in server_received
    assert b"after-migrate" in server_received
    assert b"ack-before" in client_received
    assert b"ack-after" in client_received

    client_task.cancel()
    server_task.cancel()
    await asyncio.gather(client_task, server_task, return_exceptions=True)
    await client.close()
    await server.stop()
