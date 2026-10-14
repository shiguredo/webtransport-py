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

# dwnx の DWNX_ERR_INVALID_ARGUMENT (dwnx.h)。単方向ストリームの向き違いで返る
DWNX_ERR_INVALID_ARGUMENT = -201

# イベントに載るアプリケーションエラーコード
_APP_ERROR_CODE = 0x2A


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


def test_reset_stream_notifies_peer() -> None:
    """ローカルの reset_stream がピアへ STREAM_RESET として届く"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, b"before reset", False)
    pump(client, server)
    # 事前のデータは判定に使わない
    drain_events(server)

    assert client.reset_stream(stream_id, _APP_ERROR_CODE) == 0
    pump(client, server)

    events = [event for event in drain_events(server) if event.type is qmux.EventType.STREAM_RESET]
    assert len(events) == 1
    assert events[0].stream_id == stream_id
    assert events[0].error_code == _APP_ERROR_CODE


def test_stop_sending_notifies_peer() -> None:
    """ローカルの stop_sending がピアへ STOP_SENDING として届く"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    stream_id = client.open_stream(True)
    assert stream_id >= 0

    assert client.stop_sending(stream_id, _APP_ERROR_CODE) == 0
    pump(client, server)

    events = [event for event in drain_events(server) if event.type is qmux.EventType.STOP_SENDING]
    assert len(events) == 1
    assert events[0].stream_id == stream_id
    assert events[0].error_code == _APP_ERROR_CODE


def test_close_stream_notifies_peer_in_both_directions() -> None:
    """ローカルの close_stream が RESET_STREAM と STOP_SENDING の両方を送る"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    stream_id = client.open_stream(True)
    assert stream_id >= 0

    assert client.close_stream(stream_id, _APP_ERROR_CODE) == 0
    pump(client, server)

    events = drain_events(server)
    reset_events = [event for event in events if event.type is qmux.EventType.STREAM_RESET]
    stop_events = [event for event in events if event.type is qmux.EventType.STOP_SENDING]
    assert len(reset_events) == 1
    assert reset_events[0].stream_id == stream_id
    assert reset_events[0].error_code == _APP_ERROR_CODE
    assert len(stop_events) == 1
    assert stop_events[0].stream_id == stream_id
    assert stop_events[0].error_code == _APP_ERROR_CODE


def test_stream_shutdown_rejects_wrong_direction() -> None:
    """単方向ストリームの向き違いは DWNX_ERR_INVALID_ARGUMENT になる

    ローカルの単方向ストリームには読み取り側が無く、リモートの単方向
    ストリームには書き込み側が無い (dwnx.h の契約)。
    """
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    # ローカル単方向: stop_sending (読み取り側) は拒否され、reset_stream は通る
    local_stream_id = client.open_stream(False)
    assert local_stream_id >= 0
    assert client.stop_sending(local_stream_id, 0) == DWNX_ERR_INVALID_ARGUMENT
    assert client.reset_stream(local_stream_id, 0) == 0

    # リモート単方向: reset_stream (書き込み側) は拒否され、stop_sending は通る
    remote_stream_id = server.open_stream(False)
    assert remote_stream_id >= 0
    server.send_stream_data(remote_stream_id, b"uni", False)
    pump(client, server)
    assert client.reset_stream(remote_stream_id, 0) == DWNX_ERR_INVALID_ARGUMENT
    assert client.stop_sending(remote_stream_id, 0) == 0


def test_stream_shutdown_for_unknown_stream_is_noop() -> None:
    """存在しないストリームへの終了操作は 0 を返し、何も送出しない"""
    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)
    # ハンドシェイクのイベントは判定に使わない
    drain_events(server)

    # 双方向ストリームの ID 形式 (4 の倍数) で未使用の値を使う
    unknown_stream_id = 4000
    assert client.reset_stream(unknown_stream_id, 0) == 0
    assert client.stop_sending(unknown_stream_id, 0) == 0
    assert client.close_stream(unknown_stream_id, 0) == 0

    pump(client, server)
    assert drain_events(server) == []
