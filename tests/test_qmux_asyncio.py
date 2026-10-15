"""QMux の asyncio クライアントとサーバーの E2E テスト

実ソケット (TCP / TLS) を使い、モックは使わない。QMux はトランスポート
パラメータを帯域内で交換するため、接続が確立すると `on_session_ready` (サーバー)
と `on_connected` (クライアント) が発火する。
"""

import asyncio
import contextlib
import logging
import ssl

import pytest

from webtransport import qmux
from webtransport.qmux._connection import ConnectionDriver

# テストの待ち時間の上限 (秒)
TIMEOUT = 10.0

# ストリーム終了 (RESET_STREAM / STOP_SENDING) に載せるアプリケーションエラーコード
_APP_ERROR_CODE = 0x2A

# TLS 上で QMux を動かすときの ALPN プロトコル
_ALPN_PROTOCOL = "qmux-test"

# ハンドシェイク済みの接続へ流し込む壊れた TLS レコード
# (application_data を名乗るが、本体が 0 埋めで TLS レコードとして解釈できない)
_BROKEN_TLS_RECORD = b"\x17\x03\x03\x00\x10" + b"\x00" * 16


async def _stop_server(server: qmux.Server, task: asyncio.Task[None]) -> None:
    """サーバーを停止し、実行タスクを片付ける"""
    await server.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def _create_tls_contexts(
    test_certificates: dict[str, str],
) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """テスト用の TLS のサーバー / クライアントコンテキストを作る

    自己署名証明書を使うため、クライアントはサーバー証明書を検証しない。
    QMux over TLS は ALPN でアプリケーションプロトコルを合意するため、
    両側で同じプロトコルを設定する。
    """
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(
        test_certificates["certfile"],
        test_certificates["keyfile"],
    )
    # ピアを asyncio.start_server で立てるテストでは、ここで設定した ALPN が
    # そのまま使われる (qmux.Server はコンストラクタで設定し直す)
    server_context.set_alpn_protocols([_ALPN_PROTOCOL])
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client_context.check_hostname = False
    client_context.verify_mode = ssl.CERT_NONE
    client_context.set_alpn_protocols([_ALPN_PROTOCOL])
    return server_context, client_context


def _server_port(server: asyncio.Server) -> int:
    """待ち受け中のサーバーが使っているポートを返す"""
    sockets = server.sockets
    if not sockets:
        raise RuntimeError("the server is not listening")
    return int(sockets[0].getsockname()[1])


def _send_broken_tls_record(writer: asyncio.StreamWriter) -> None:
    """TLS 層を迂回し、生ソケットから壊れた TLS レコードを送る

    生ソケットを複製して直接書くため、SSL 層による暗号化やレコード分割を
    受けずにピアへ届き、ピアの TLS 層はレコード解析に失敗する。
    """
    raw_socket = writer.get_extra_info("socket")
    if raw_socket is None:
        raise RuntimeError("the stream is not backed by a socket")
    duplicated_socket = raw_socket.dup()
    try:
        duplicated_socket.sendall(_BROKEN_TLS_RECORD)
    finally:
        duplicated_socket.close()


def _has_warning(caplog: pytest.LogCaptureFixture, message: str) -> bool:
    """指定の文字列を含む警告ログが記録されているかどうかを返す"""
    return any(
        record.levelno == logging.WARNING and message in record.getMessage()
        for record in caplog.records
    )


async def _wait_for_warning(
    caplog: pytest.LogCaptureFixture,
    message: str,
    timeout: float,
) -> bool:
    """指定の文字列を含む警告ログが記録されるまで待つ

    ログを書くのは接続を処理する別タスクのため、テスト本体からは完了を
    直接待てない。記録が現れるかタイムアウトするまで一定間隔で確認する。

    Returns:
        記録された場合は True、タイムアウトした場合は False
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if _has_warning(caplog, message):
            return True
        await asyncio.sleep(0.05)
    return _has_warning(caplog, message)


@pytest.mark.asyncio
async def test_stream_data_is_echoed_over_tcp() -> None:
    """TCP 上の QMux で双方向ストリームのデータが往復する"""
    server = qmux.Server("127.0.0.1", 0)
    server_received: list[tuple[int, bytes, bool]] = []

    async def on_server_stream_data(
        session: qmux.Session,
        stream_id: int,
        data: bytes,
        fin: bool,
    ) -> None:
        server_received.append((stream_id, data, fin))
        # 双方向ストリーム (stream_id % 4 == 0) のみエコーする
        if stream_id % 4 == 0:
            await session.send_stream_data(stream_id, data, fin=fin)

    server.on_stream_data(on_server_stream_data)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port)
    echoed: list[bytes] = []
    echo_received = asyncio.Event()

    async def on_client_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        echoed.append(data)
        echo_received.set()

    client.on_stream_data(on_client_stream_data)
    client_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        client_task = asyncio.create_task(client.run())
        stream_id = await client.open_stream()
        assert stream_id >= 0
        await client.send_stream_data(stream_id, b"hello qmux", fin=True)
        await asyncio.wait_for(echo_received.wait(), TIMEOUT)
        assert echoed == [b"hello qmux"]
        assert server_received == [(stream_id, b"hello qmux", True)]
    finally:
        await client.close()
        if client_task is not None:
            await asyncio.wait_for(client_task, TIMEOUT)
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_server_opens_unidirectional_stream() -> None:
    """サーバーから単方向ストリームを開いて送信できる"""
    server = qmux.Server("127.0.0.1", 0)
    server_stream_id = -1

    async def on_session_ready(session: qmux.Session) -> None:
        nonlocal server_stream_id
        server_stream_id = await session.open_stream(unidirectional=True)
        await session.send_stream_data(server_stream_id, b"from server", fin=True)

    server.on_session_ready(on_session_ready)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port)
    received: list[tuple[int, bytes, bool]] = []
    received_event = asyncio.Event()

    async def on_client_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        received.append((stream_id, data, fin))
        received_event.set()

    client.on_stream_data(on_client_stream_data)
    client_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        client_task = asyncio.create_task(client.run())
        await asyncio.wait_for(received_event.wait(), TIMEOUT)
        assert received == [(server_stream_id, b"from server", True)]
        # サーバーが開いたのは単方向ストリーム (stream_id % 4 == 2 か 3)
        assert server_stream_id % 4 in (2, 3)
    finally:
        await client.close()
        if client_task is not None:
            await asyncio.wait_for(client_task, TIMEOUT)
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_client_close_notifies_server() -> None:
    """クライアントが閉じるとサーバーの on_session_closed が発火する"""
    server = qmux.Server("127.0.0.1", 0)
    closed_sessions: list[int] = []
    closed_event = asyncio.Event()

    async def on_session_closed(session: qmux.Session) -> None:
        closed_sessions.append(session.session_id)
        closed_event.set()

    server.on_session_closed(on_session_closed)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port, close_reason="bye")
    try:
        await client.connect()
        assert client.is_connected is True
        client_task = asyncio.create_task(client.run())
        await client.close()
        await asyncio.wait_for(client_task, TIMEOUT)
        await asyncio.wait_for(closed_event.wait(), TIMEOUT)
        assert closed_sessions == [1]
    finally:
        await client.close()
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_handshake_completes_on_both_sides() -> None:
    """接続直後に両側でハンドシェイクが完了する"""
    server = qmux.Server("127.0.0.1", 0)
    server_ready = asyncio.Event()

    async def on_session_ready(session: qmux.Session) -> None:
        server_ready.set()

    server.on_session_ready(on_session_ready)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port)
    connected = asyncio.Event()

    async def on_connected() -> None:
        connected.set()

    client.on_connected(on_connected)
    try:
        # connect() はトランスポートパラメータの交換が終わるまで待つ
        await client.connect()
        assert connected.is_set()
        await asyncio.wait_for(server_ready.wait(), TIMEOUT)
        assert client.peer_address is not None
        assert client.peer_address[0] == "127.0.0.1"
        assert client.negotiated_alpn_protocol is None
    finally:
        await client.close()
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_tls_with_alpn(test_certificates: dict[str, str]) -> None:
    """TLS 上で ALPN を交渉して QMux を動かせる"""
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(
        test_certificates["certfile"],
        test_certificates["keyfile"],
    )
    client_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # 自己署名証明書を使うため検証しない (ALPN の交渉だけを確認する)
    client_context.check_hostname = False
    client_context.verify_mode = ssl.CERT_NONE

    server = qmux.Server(
        "127.0.0.1",
        0,
        ssl=server_context,
        alpn_protocols=["qmux-test"],
    )
    received: list[bytes] = []
    received_event = asyncio.Event()

    async def on_server_stream_data(
        session: qmux.Session,
        stream_id: int,
        data: bytes,
        fin: bool,
    ) -> None:
        received.append(data)
        received_event.set()

    server.on_stream_data(on_server_stream_data)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client(
        "127.0.0.1",
        server.actual_port,
        ssl=client_context,
        alpn_protocols=["qmux-test"],
    )
    client_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        assert client.negotiated_alpn_protocol == "qmux-test"
        client_task = asyncio.create_task(client.run())
        stream_id = await client.open_stream()
        await client.send_stream_data(stream_id, b"over tls", fin=True)
        await asyncio.wait_for(received_event.wait(), TIMEOUT)
        assert received == [b"over tls"]
    finally:
        await client.close()
        if client_task is not None:
            await asyncio.wait_for(client_task, TIMEOUT)
        await _stop_server(server, server_task)


def test_alpn_protocols_is_required_with_ssl() -> None:
    """ssl を渡したのに alpn_protocols が無い場合は ValueError になる"""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with pytest.raises(ValueError, match="alpn_protocols"):
        qmux.Client("127.0.0.1", 4433, ssl=context)
    with pytest.raises(ValueError, match="alpn_protocols"):
        qmux.Server("127.0.0.1", 4433, ssl=context)


def test_alpn_protocols_requires_ssl() -> None:
    """alpn_protocols だけを渡した場合は ValueError になる"""
    with pytest.raises(ValueError, match="requires ssl"):
        qmux.Client("127.0.0.1", 4433, alpn_protocols=["qmux"])
    with pytest.raises(ValueError, match="requires ssl"):
        qmux.Server("127.0.0.1", 4433, alpn_protocols=["qmux"])


@pytest.mark.asyncio
async def test_run_before_connect_raises() -> None:
    """connect() の前に run() や send_stream_data() を呼ぶと RuntimeError になる"""
    client = qmux.Client("127.0.0.1", 4433)
    with pytest.raises(RuntimeError, match="connect"):
        await client.run()
    with pytest.raises(RuntimeError, match="connect"):
        await client.send_stream_data(0, b"data")
    with pytest.raises(RuntimeError, match="connect"):
        await client.reset_stream(0)
    with pytest.raises(RuntimeError, match="connect"):
        await client.stop_sending(0)
    with pytest.raises(RuntimeError, match="connect"):
        await client.close_stream(0)


@pytest.mark.asyncio
async def test_server_accepts_multiple_connections() -> None:
    """複数の接続を同時に受け付けられる"""
    server = qmux.Server("127.0.0.1", 0)
    session_ids: list[int] = []
    sessions_event = asyncio.Event()

    async def on_session_ready(session: qmux.Session) -> None:
        session_ids.append(session.session_id)
        if len(session_ids) >= 2:
            sessions_event.set()

    server.on_session_ready(on_session_ready)

    await server.start()
    server_task = asyncio.create_task(server.run())

    clients = [
        qmux.Client("127.0.0.1", server.actual_port),
        qmux.Client("127.0.0.1", server.actual_port),
    ]
    try:
        for client in clients:
            await client.connect()
        await asyncio.wait_for(sessions_event.wait(), TIMEOUT)
        assert sorted(session_ids) == [1, 2]
    finally:
        for client in clients:
            await client.close()
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_client_stop_sending_notifies_server() -> None:
    """クライアントの stop_sending がサーバーの on_stop_sending で観測できる"""
    server = qmux.Server("127.0.0.1", 0)
    stop_sending: list[tuple[int, int]] = []
    stop_sending_event = asyncio.Event()

    async def on_server_stop_sending(
        session: qmux.Session,
        stream_id: int,
        error_code: int,
    ) -> None:
        stop_sending.append((stream_id, error_code))
        stop_sending_event.set()

    server.on_stop_sending(on_server_stop_sending)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port)
    client_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        client_task = asyncio.create_task(client.run())
        stream_id = await client.open_stream()
        assert stream_id >= 0
        # 双方向ストリームを両側に知らせてから停止を要求する
        await client.send_stream_data(stream_id, b"hello", fin=False)
        await client.stop_sending(stream_id, _APP_ERROR_CODE)
        await asyncio.wait_for(stop_sending_event.wait(), TIMEOUT)
        assert stop_sending == [(stream_id, _APP_ERROR_CODE)]
    finally:
        await client.close()
        if client_task is not None:
            await asyncio.wait_for(client_task, TIMEOUT)
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_server_reset_stream_notifies_client() -> None:
    """サーバーの reset_stream がクライアントの on_stream_reset で観測できる"""
    server = qmux.Server("127.0.0.1", 0)
    sessions: list[qmux.Session] = []
    server_stream_ids: list[int] = []
    data_received = asyncio.Event()

    async def on_session_ready(session: qmux.Session) -> None:
        sessions.append(session)
        stream_id = await session.open_stream()
        assert stream_id >= 0
        server_stream_ids.append(stream_id)
        # 送信側を開いたまま (FIN を付けずに) データを送る
        await session.send_stream_data(stream_id, b"from server", fin=False)

    server.on_session_ready(on_session_ready)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port)
    reset_events: list[tuple[int, int]] = []
    reset_event = asyncio.Event()

    async def on_client_stream_data(stream_id: int, data: bytes, fin: bool) -> None:
        data_received.set()

    async def on_client_stream_reset(stream_id: int, error_code: int) -> None:
        reset_events.append((stream_id, error_code))
        reset_event.set()

    client.on_stream_data(on_client_stream_data)
    client.on_stream_reset(on_client_stream_reset)
    client_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        client_task = asyncio.create_task(client.run())
        await asyncio.wait_for(data_received.wait(), TIMEOUT)
        assert server_stream_ids
        await sessions[0].reset_stream(server_stream_ids[0], _APP_ERROR_CODE)
        await asyncio.wait_for(reset_event.wait(), TIMEOUT)
        assert reset_events == [(server_stream_ids[0], _APP_ERROR_CODE)]
    finally:
        await client.close()
        if client_task is not None:
            await asyncio.wait_for(client_task, TIMEOUT)
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_client_close_stream_notifies_server_in_both_directions() -> None:
    """クライアントの close_stream がサーバーで RESET_STREAM と STOP_SENDING の両方になる"""
    server = qmux.Server("127.0.0.1", 0)
    reset_events: list[tuple[int, int]] = []
    stop_events: list[tuple[int, int]] = []
    both_received = asyncio.Event()

    def _set_if_both_received() -> None:
        if reset_events and stop_events:
            both_received.set()

    async def on_server_stream_reset(
        session: qmux.Session,
        stream_id: int,
        error_code: int,
    ) -> None:
        reset_events.append((stream_id, error_code))
        _set_if_both_received()

    async def on_server_stop_sending(
        session: qmux.Session,
        stream_id: int,
        error_code: int,
    ) -> None:
        stop_events.append((stream_id, error_code))
        _set_if_both_received()

    server.on_stream_reset(on_server_stream_reset)
    server.on_stop_sending(on_server_stop_sending)

    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port)
    client_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        client_task = asyncio.create_task(client.run())
        stream_id = await client.open_stream()
        assert stream_id >= 0
        await client.send_stream_data(stream_id, b"hello", fin=False)
        await client.close_stream(stream_id, _APP_ERROR_CODE)
        await asyncio.wait_for(both_received.wait(), TIMEOUT)
        assert reset_events == [(stream_id, _APP_ERROR_CODE)]
        assert stop_events == [(stream_id, _APP_ERROR_CODE)]
    finally:
        await client.close()
        if client_task is not None:
            await asyncio.wait_for(client_task, TIMEOUT)
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_direction_mismatch_raises_protocol_error() -> None:
    """単方向ストリームの向き違いは QmuxProtocolError になる

    ローカルの単方向ストリームには読み取り側が無いため stop_sending は拒否され、
    書き込み側の reset_stream は通る (dwnx.h の契約)。
    """
    server = qmux.Server("127.0.0.1", 0)
    await server.start()
    server_task = asyncio.create_task(server.run())

    client = qmux.Client("127.0.0.1", server.actual_port)
    client_task: asyncio.Task[None] | None = None
    try:
        await client.connect()
        client_task = asyncio.create_task(client.run())
        stream_id = await client.open_stream(unidirectional=True)
        assert stream_id >= 0
        with pytest.raises(qmux.exceptions.QmuxProtocolError):
            await client.stop_sending(stream_id, 0)
        # 書き込み側は存在するため受理される
        await client.reset_stream(stream_id, 0)
    finally:
        await client.close()
        if client_task is not None:
            await asyncio.wait_for(client_task, TIMEOUT)
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_client_run_reports_tls_failure_as_connection_error(
    test_certificates: dict[str, str],
) -> None:
    """ピアの TLS 失敗を run() が接続断として報告する

    ピアが壊れた TLS レコードを送ると TLS 層が失敗する。QMux は TLS 上で
    動くため、この失敗を「接続が切れた」として扱う。run() が生の
    ssl.SSLError を漏らさず、原因の例外を __cause__ に残した
    QmuxConnectionError を送出することを確認する。
    """
    server_context, client_context = _create_tls_contexts(test_certificates)
    # クライアントの connect() が終わったことをピアへ知らせる
    client_connected = asyncio.Event()

    async def on_peer_connection(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            # ピア側は QMux のサーバーとしてハンドシェイクを終える
            driver = ConnectionDriver(qmux.Connection.create_server(qmux.Config()), reader, writer)
            await asyncio.wait_for(driver.wait_ready(TIMEOUT), TIMEOUT)
            # クライアントが読み始める前にレコードを送らないよう待つ
            await asyncio.wait_for(client_connected.wait(), TIMEOUT)
            # TLS 層を迂回して壊れたレコードを送る
            _send_broken_tls_record(writer)
        finally:
            writer.close()

    peer_server = await asyncio.start_server(
        on_peer_connection,
        "127.0.0.1",
        0,
        ssl=server_context,
    )
    client = qmux.Client(
        "127.0.0.1",
        _server_port(peer_server),
        ssl=client_context,
        alpn_protocols=[_ALPN_PROTOCOL],
    )
    try:
        await client.connect()
        client_connected.set()
        with pytest.raises(qmux.exceptions.QmuxConnectionError) as exc_info:
            await asyncio.wait_for(client.run(), TIMEOUT)
        # TLS の失敗が原因の例外として残っている
        cause = exc_info.value.__cause__
        assert isinstance(cause, ssl.SSLError), f"__cause__ が ssl.SSLError ではない: {cause!r}"
    finally:
        await client.close()
        peer_server.close()
        await peer_server.wait_closed()


@pytest.mark.asyncio
async def test_client_close_suppresses_tls_failure_during_shutdown(
    test_certificates: dict[str, str],
) -> None:
    """TLS 終了処理中にピアがデータを送っても close() が ssl.SSLError を漏らさない

    close() は CONNECTION_CLOSE を送ったあと close_notify を送り、TLS の終了を
    待つ。その間にピアが壊れたレコードを送ると TLS 層が失敗するが、QMux は
    これを「接続が切れた」として扱うため close() から例外が漏れないことを
    確認する。
    """
    server_context, client_context = _create_tls_contexts(test_certificates)
    # クライアントの connect() が終わったことをピアへ知らせる
    client_connected = asyncio.Event()
    # ピアが壊れたレコードを送り終えたことをテスト本体へ知らせる
    record_sent = asyncio.Event()
    # close() が終わるまでピアが接続を開いたままにするための合図
    peer_release = asyncio.Event()

    async def on_peer_connection(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            driver = ConnectionDriver(qmux.Connection.create_server(qmux.Config()), reader, writer)
            await asyncio.wait_for(driver.wait_ready(TIMEOUT), TIMEOUT)
            await asyncio.wait_for(client_connected.wait(), TIMEOUT)
            # クライアントの close_notify の直後に届くよう、生ソケットから送る
            _send_broken_tls_record(writer)
            record_sent.set()
            # close_notify を返さずに開いたままにして、close() の終了を待たせる
            await asyncio.wait_for(peer_release.wait(), TIMEOUT)
        finally:
            writer.close()

    peer_server = await asyncio.start_server(
        on_peer_connection,
        "127.0.0.1",
        0,
        ssl=server_context,
    )
    client = qmux.Client(
        "127.0.0.1",
        _server_port(peer_server),
        ssl=client_context,
        alpn_protocols=[_ALPN_PROTOCOL],
    )
    try:
        await client.connect()
        client_connected.set()
        await asyncio.wait_for(record_sent.wait(), TIMEOUT)
        try:
            await asyncio.wait_for(client.close(), TIMEOUT)
        except ssl.SSLError as exc:
            pytest.fail(f"close() から ssl.SSLError が漏れた: {exc!r}")
        finally:
            peer_release.set()
    finally:
        await client.close()
        peer_server.close()
        await peer_server.wait_closed()


@pytest.mark.asyncio
async def test_server_logs_tls_failure_as_connection_error(
    test_certificates: dict[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """ピアの TLS 失敗をサーバーが接続断の警告ログに記録する

    サーバーは接続ごとのタスクで run() を回す。ピアが壊れた TLS レコードを
    送って TLS 層が失敗したとき、接続ハンドラのタスクへ未処理例外を漏らさず、
    接続断として警告ログに記録することを確認する。
    """
    server_context, client_context = _create_tls_contexts(test_certificates)
    server = qmux.Server(
        "127.0.0.1",
        0,
        ssl=server_context,
        alpn_protocols=[_ALPN_PROTOCOL],
    )
    await server.start()
    server_task = asyncio.create_task(server.run())

    peer_writer: asyncio.StreamWriter | None = None
    try:
        # テスト側は QMux のクライアントとして振る舞う
        reader, writer = await asyncio.open_connection(
            "127.0.0.1",
            server.actual_port,
            ssl=client_context,
            server_hostname="127.0.0.1",
        )
        peer_writer = writer
        driver = ConnectionDriver(qmux.Connection.create_client(qmux.Config()), reader, writer)
        await asyncio.wait_for(driver.wait_ready(TIMEOUT), TIMEOUT)
        _send_broken_tls_record(writer)

        # サーバーの接続ハンドラが接続断を警告ログに記録するまで待つ
        assert await _wait_for_warning(caplog, "qmux connection error", TIMEOUT), (
            "サーバーが接続断の警告ログを記録しなかった: "
            f"{[record.getMessage() for record in caplog.records]}"
        )
        # 接続ハンドラのタスクへ未処理例外が漏れていない
        unhandled = [
            record.getMessage()
            for record in caplog.records
            if record.levelno >= logging.ERROR and "Unhandled exception" in record.getMessage()
        ]
        assert unhandled == [], f"接続ハンドラで未処理例外が発生した: {unhandled}"
    finally:
        if peer_writer is not None:
            peer_writer.close()
        await _stop_server(server, server_task)


@pytest.mark.asyncio
async def test_client_connect_reports_tls_failure_as_connection_error(
    test_certificates: dict[str, str],
) -> None:
    """QMux ハンドシェイク中の TLS 失敗を connect() が接続断として報告する

    TLS ハンドシェイクは完了するが QMux のトランスポートパラメータを送らない
    ピアが壊れた TLS レコードを送ると、connect() が待っている間に TLS 層が
    失敗する。connect() が生の ssl.SSLError を漏らさず、原因の例外を
    __cause__ に残した QmuxConnectionError を送出し、失敗した接続を残さない
    ことを確認する。
    """
    server_context, client_context = _create_tls_contexts(test_certificates)
    # ピアが壊れたレコードを送るまで、接続を開いたままにするための合図
    peer_release = asyncio.Event()

    async def on_peer_connection(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            # QMux のハンドシェイクは進めず、クライアントがトランスポート
            # パラメータを送ったことだけを確認する
            await asyncio.wait_for(reader.read(1), TIMEOUT)
            # TLS 層を迂回して壊れたレコードを送る
            _send_broken_tls_record(writer)
            await asyncio.wait_for(peer_release.wait(), TIMEOUT)
        finally:
            writer.close()

    peer_server = await asyncio.start_server(
        on_peer_connection,
        "127.0.0.1",
        0,
        ssl=server_context,
    )
    client = qmux.Client(
        "127.0.0.1",
        _server_port(peer_server),
        ssl=client_context,
        alpn_protocols=[_ALPN_PROTOCOL],
    )
    try:
        with pytest.raises(qmux.exceptions.QmuxConnectionError) as exc_info:
            await asyncio.wait_for(client.connect(), TIMEOUT)
        # TLS の失敗が原因の例外として残っている
        cause = exc_info.value.__cause__
        assert isinstance(cause, ssl.SSLError), f"__cause__ が ssl.SSLError ではない: {cause!r}"
        # 失敗した接続は接続中として残らない
        assert client.is_connected is False, (
            "ハンドシェイクに失敗したクライアントが接続中になっている"
        )
        # 後始末の close() は壊れたトランスポートへ書けないだけで、例外を漏らさない
        await asyncio.wait_for(client.close(), TIMEOUT)
    finally:
        peer_release.set()
        try:
            # 失敗経路でもトランスポートを残さない (元の失敗を隠さないよう、
            # すでに切れた接続の例外だけを無視する)
            await client.close()
        except ssl.SSLError:
            pass
        peer_server.close()
        await peer_server.wait_closed()


@pytest.mark.asyncio
async def test_client_close_suppresses_tls_failure_on_broken_transport(
    test_certificates: dict[str, str],
) -> None:
    """run() を回していない間にトランスポートが壊れても close() が ssl.SSLError を漏らさない

    接続確立後に run() を回さず、ピアが壊れた TLS レコードを送ると、クライアントの
    TLS 層はレコードを処理して接続を閉じる。その後に呼ぶ close() は、すでに
    切れたトランスポートへ CONNECTION_CLOSE を書けないが、例外を漏らさずに
    接続を閉じることを確認する。
    """
    server_context, client_context = _create_tls_contexts(test_certificates)
    # クライアントの connect() が終わったことをピアへ知らせる
    client_connected = asyncio.Event()
    # クライアントのトランスポートが壊れたことをテスト本体へ知らせる
    transport_broken = asyncio.Event()

    async def on_peer_connection(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            driver = ConnectionDriver(qmux.Connection.create_server(qmux.Config()), reader, writer)
            await asyncio.wait_for(driver.wait_ready(TIMEOUT), TIMEOUT)
            await asyncio.wait_for(client_connected.wait(), TIMEOUT)
            _send_broken_tls_record(writer)
            # クライアントは run() を回していないため、TLS 層がレコードを処理して
            # 接続を閉じたことを EOF (または読み取りの失敗) で観測する
            try:
                await asyncio.wait_for(reader.read(1), TIMEOUT)
            except OSError:
                # クライアントの TLS 層が失敗した場合も、接続が壊れた証拠になる
                pass
            transport_broken.set()
        finally:
            writer.close()

    peer_server = await asyncio.start_server(
        on_peer_connection,
        "127.0.0.1",
        0,
        ssl=server_context,
    )
    client = qmux.Client(
        "127.0.0.1",
        _server_port(peer_server),
        ssl=client_context,
        alpn_protocols=[_ALPN_PROTOCOL],
    )
    try:
        await client.connect()
        client_connected.set()
        # クライアントのトランスポートが壊れるまで待つ
        await asyncio.wait_for(transport_broken.wait(), TIMEOUT)
        try:
            await asyncio.wait_for(client.close(), TIMEOUT)
        except ssl.SSLError as exc:
            pytest.fail(f"close() から ssl.SSLError が漏れた: {exc!r}")
        assert client.is_connected is False, "close() の後もクライアントが接続中になっている"
    finally:
        try:
            await client.close()
        except ssl.SSLError:
            pass
        peer_server.close()
        await peer_server.wait_closed()


@pytest.mark.asyncio
async def test_client_connect_cancellation_clears_driver(
    test_certificates: dict[str, str],
) -> None:
    """QMux ハンドシェイク待ちの connect() をキャンセルしても接続が残らない

    CancelledError は QmuxConnectionError ではないため、キャンセルされた
    connect() がドライバを残すと、後続の close() が生きたままのトランスポートへ
    書き込んで失敗する。キャンセル時もドライバを手放し、close() が例外を
    出さないことを確認する。
    """
    server_context, client_context = _create_tls_contexts(test_certificates)
    # ピアが TLS ハンドシェイクを終えたことをテスト本体へ知らせる
    peer_tls_ready = asyncio.Event()
    # ピアが接続を開いたままにするための合図
    peer_release = asyncio.Event()

    async def on_peer_connection(
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            # QMux のトランスポートパラメータは送らず、接続だけを開いておく
            peer_tls_ready.set()
            await asyncio.wait_for(peer_release.wait(), TIMEOUT)
        finally:
            writer.close()

    peer_server = await asyncio.start_server(
        on_peer_connection,
        "127.0.0.1",
        0,
        ssl=server_context,
    )
    client = qmux.Client(
        "127.0.0.1",
        _server_port(peer_server),
        ssl=client_context,
        alpn_protocols=[_ALPN_PROTOCOL],
    )
    try:
        connect_task = asyncio.create_task(client.connect())
        await asyncio.wait_for(peer_tls_ready.wait(), TIMEOUT)
        # ドライバができて QMux のハンドシェイク待ちに入るまで待つ
        loop = asyncio.get_running_loop()
        deadline = loop.time() + TIMEOUT
        while not client.is_connected:
            assert loop.time() < deadline, "クライアントのドライバが作られなかった"
            await asyncio.sleep(0)
        # ハンドシェイクが終わらないうちにキャンセルする
        connect_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await connect_task
        # キャンセルした接続は接続中として残らない
        assert client.is_connected is False, "キャンセルした接続のドライバが残っている"
        # 後始末の close() が例外を出さない
        await asyncio.wait_for(client.close(), TIMEOUT)
    finally:
        peer_release.set()
        peer_server.close()
        await peer_server.wait_closed()
