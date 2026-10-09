"""QMux (dwnx) バインディングの検証

2 つの `qmux.Connection` をメモリ上のバイト列で直結し、TLS 無しで
ハンドシェイク (トランスポートパラメータ交換)、ストリーム、タイマー、
接続クローズが動くことを検証する。モックは使わず、実際の dwnx の接続同士を
相手にする。
"""

from webtransport import qmux

# dwnx の DWNX_ERR_DRAINING (dwnx.h)。ピアの CONNECTION_CLOSE を受信した後は
# この値が返る
DWNX_ERR_DRAINING = -224


def pump(first: qmux.Connection, second: qmux.Connection, rounds: int = 100) -> None:
    """2 つの接続の間でレコードを交換し尽くす

    Args:
        first: 片側の接続
        second: もう片側の接続
        rounds: 交換の最大回数 (無限ループを避ける)
    """
    for _ in range(rounds):
        moved = False
        for source, destination in ((first, second), (second, first)):
            record = source.pending_record
            if record:
                destination.receive(record)
                moved = True
            if source.timeout == 0:
                source.handle_timeout()
                moved = True
        if not moved:
            break


def drain_events(connection: qmux.Connection) -> list[qmux.Event]:
    """接続に溜まったイベントを取り出す"""
    events: list[qmux.Event] = []
    while (event := connection.next_event()) is not None:
        events.append(event)
    return events


def test_get_version_returns_dwnx_version() -> None:
    """dwnx のバージョン文字列が取得できる"""
    assert qmux.get_version() != ""
    assert isinstance(qmux.get_version(), str)


def test_create_client_and_server() -> None:
    """クライアントとサーバーの接続を作成できる"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    assert client.is_server is False
    assert server.is_server is True

    # 開設可能なストリーム数はピアのトランスポートパラメータ受信後に確定する
    pump(client, server)
    assert client.streams_bidi_left > 0
    assert client.streams_uni_left > 0


def test_handshake_exchanges_transport_params() -> None:
    """トランスポートパラメータの交換が TLS 無しで完了する"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)

    pump(client, server)

    client_events = drain_events(client)
    server_events = drain_events(server)
    assert qmux.EventType.TRANSPORT_PARAMS_RECEIVED in [event.type for event in client_events]
    assert qmux.EventType.TRANSPORT_PARAMS_RECEIVED in [event.type for event in server_events]


def test_bidirectional_stream_delivers_data_once() -> None:
    """双方向ストリームのデータが 1 回だけ届く"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, b"hello qmux", False)
    pump(client, server)

    events = [event for event in drain_events(server) if event.type is qmux.EventType.STREAM_DATA]
    assert len(events) == 1
    assert events[0].stream_id == stream_id
    assert bytes(events[0].data) == b"hello qmux"


def test_unidirectional_stream_delivers_data() -> None:
    """単方向ストリームのデータが届く"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    stream_id = client.open_stream(False)
    assert stream_id >= 0
    client.send_stream_data(stream_id, b"uni", True)
    pump(client, server)

    events = [event for event in drain_events(server) if event.type is qmux.EventType.STREAM_DATA]
    assert len(events) == 1
    assert bytes(events[0].data) == b"uni"


def test_receive_accepts_split_record() -> None:
    """レコードが 1 バイトずつ分割されて届いても同じ結果になる"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    stream_id = client.open_stream(True)
    client.send_stream_data(stream_id, b"split record", False)

    record = client.pending_record
    assert record is not None
    for index in range(len(record)):
        server.receive(record[index : index + 1])

    events = [event for event in drain_events(server) if event.type is qmux.EventType.STREAM_DATA]
    # 分割して届いた分は、届いた粒度で通知される (重複はしない)
    assert b"".join(bytes(event.data) for event in events) == b"split record"


def test_large_stream_data_is_delivered_in_records() -> None:
    """レコード長を超えるデータが複数レコードに分割されて届く"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    payload = bytes(range(256)) * 400
    stream_id = client.open_stream(True)
    client.send_stream_data(stream_id, payload, False)
    pump(client, server, rounds=400)

    received = b"".join(
        bytes(event.data)
        for event in drain_events(server)
        if event.type is qmux.EventType.STREAM_DATA
    )
    assert received == payload


def test_timeout_is_scheduled_and_handled() -> None:
    """タイマーが設定され、処理しても例外にならない"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    timeout = client.timeout
    assert timeout is not None
    client.handle_timeout()
    # タイマー処理後も接続は使える
    assert client.open_stream(True) >= 0


def test_close_writes_connection_close() -> None:
    """close() が CONNECTION_CLOSE のレコードを生成する"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    client.close(0, "bye")
    record = client.pending_record
    assert record is not None
    assert len(record) > 0
    # 相手は CONNECTION_CLOSE を処理して draining へ遷移する (DWNX_ERR_DRAINING)
    assert server.receive(record) == DWNX_ERR_DRAINING
