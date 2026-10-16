"""QMux (dwnx) バインディングの検証

2 つの `qmux.Connection` をメモリ上のバイト列で直結し、TLS 無しで
ハンドシェイク (トランスポートパラメータ交換)、ストリーム、タイマー、
接続クローズが動くことを検証する。モックは使わず、実際の dwnx の接続同士を
相手にする。
"""

from collections.abc import Callable

import pytest
from qmux_relay import parse_connection_close

from webtransport import qmux

# dwnx の DWNX_ERR_DRAINING (dwnx.h)。ピアの CONNECTION_CLOSE を受信した後は
# この値が返る
DWNX_ERR_DRAINING = -224

# dwnx の DWNX_ERR_INVALID_ARGUMENT (dwnx.h)。単方向ストリームの向き違いで返る
DWNX_ERR_INVALID_ARGUMENT = -201

# イベントに載るアプリケーションエラーコード
_APP_ERROR_CODE = 0x2A

# 接続終了 (CONNECTION_CLOSE) に載せるアプリケーションエラーコード
_CLOSE_ERROR_CODE = 0x2B

# 1 つの QMux レコードの最大長。dwnx はレコード長 (2 バイト) と Frames フィールド
# (DWNX_DEFAULT_MAX_RECORD_SIZE の 16382 バイト) の合計までしか書かない
_MAX_RECORD_SIZE = 16384

# QUIC varint で表せる最大値 (RFC 9000 Section 16)。終了コードの上限でもある
_MAX_VARINT = 2**62 - 1

# dwnx が固定する 1 レコードの最大長の下限 (DWNX_DEFAULT_MAX_RECORD_SIZE)
_MIN_RECORD_SIZE = 16382

# 広告できるストリーム数の上限 (RFC 9000 Section 4.6)
_MAX_STREAMS = 2**60


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


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("initial_max_streams_bidi", _MAX_VARINT + 1),
        ("initial_max_streams_uni", _MAX_VARINT + 1),
        ("initial_max_streams_bidi", _MAX_STREAMS + 1),
        ("initial_max_streams_uni", _MAX_STREAMS + 1),
        ("initial_max_data", _MAX_VARINT + 1),
        ("initial_max_stream_data_bidi_local", _MAX_VARINT + 1),
        ("initial_max_stream_data_bidi_remote", _MAX_VARINT + 1),
        ("initial_max_stream_data_uni", _MAX_VARINT + 1),
        ("max_record_size", _MAX_VARINT + 1),
        ("max_record_size", _MIN_RECORD_SIZE - 1),
        ("max_idle_timeout_ns", 2**64 - 1),
    ],
    ids=[
        "streams-bidi-over-varint",
        "streams-uni-over-varint",
        "streams-bidi-over-max-streams",
        "streams-uni-over-max-streams",
        "max-data-over-varint",
        "stream-data-bidi-local-over-varint",
        "stream-data-bidi-remote-over-varint",
        "stream-data-uni-over-varint",
        "record-size-over-varint",
        "record-size-under-minimum",
        "idle-timeout-uint64-max",
    ],
)
def test_invalid_config_raises_value_error(field_name: str, value: int) -> None:
    """範囲外の設定値が ValueError になり、プロセスが abort しない

    dwnx は `conn_new` とトランスポートパラメータの符号化で設定値を assert で
    検査する。同梱の libdwnx は NDEBUG なしでビルドされているため、範囲外の値は
    Python の例外ではなくプロセスの abort (SIGABRT) になる。接続を作る前に
    ValueError にして、Python から渡された値でプロセスを落とさないようにする。

    検査する条件は dwnx の assert と同じで、QUIC varint の上限 (2^62-1) を
    超えないこと、ストリーム数が 2^60 以下であること、`max_record_size` が
    dwnx の下限 (16382) 以上であること、`max_idle_timeout_ns` が UINT64_MAX
    (dwnx が「タイムアウト無し」に使う値) でないことである。`create_client` と
    `create_server` は同じ初期化を通る。
    """
    config = qmux.Config()
    # フィールド名でパラメトライズしているため動的に設定する
    setattr(config, field_name, value)
    with pytest.raises(ValueError):
        qmux.Connection.create_server(config)


def test_valid_config_boundary_values_are_accepted() -> None:
    """上限ちょうどの設定値で接続を作り、ハンドシェイクできる

    `initial_max_streams_bidi` / `initial_max_streams_uni` は 2^60、その他の値は
    QUIC varint の上限ちょうど (2^62-1)、`max_record_size` は dwnx の下限ちょうど
    (16382)、`max_idle_timeout_ns` は UINT64_MAX の 1 つ手前まで受け付ける。
    値域の検査が有効な設定まで弾いていないことを、レコードを作れることだけで
    なくハンドシェイクの完了まで確かめる (ストリーム数は 2^60 を超えると dwnx の
    デコーダがピアを拒否するため、ハンドシェイクまで見る必要がある)。
    """
    config = qmux.Config()
    config.initial_max_streams_bidi = _MAX_STREAMS
    config.initial_max_streams_uni = _MAX_STREAMS
    config.initial_max_data = _MAX_VARINT
    config.initial_max_stream_data_bidi_local = _MAX_VARINT
    config.initial_max_stream_data_bidi_remote = _MAX_VARINT
    config.initial_max_stream_data_uni = _MAX_VARINT
    config.max_record_size = _MIN_RECORD_SIZE
    config.max_idle_timeout_ns = 2**64 - 2

    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    # トランスポートパラメータの交換が完了すると、開設可能なストリーム数が
    # ピアの広告した値で確定する
    assert client.streams_bidi_left == _MAX_STREAMS
    assert server.streams_bidi_left == _MAX_STREAMS
    assert client.streams_uni_left == _MAX_STREAMS
    assert server.streams_uni_left == _MAX_STREAMS


def test_large_max_record_size_does_not_reserve_memory() -> None:
    """`max_record_size` を大きくしても送信バッファの確保が増えない

    dwnx は送信するレコード長を 16382 バイトに固定し、アプリケーションから
    引き上げさせないため、送信バッファもその大きさで足りる。設定値をそのまま
    確保に使うと、大きな値を指定した利用者がメモリを使い果たす。
    """
    config = qmux.Config()
    config.max_record_size = _MAX_VARINT

    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)
    pump(client, server)

    # ストリームを開いてレコードを書けることまで確かめる
    stream_id = client.open_stream(True)
    assert stream_id >= 0
    client.send_stream_data(stream_id, b"hello", True)
    assert client.pending_record is not None


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


@pytest.mark.parametrize("side", ["client", "server"], ids=["client", "server"])
@pytest.mark.parametrize(
    "reason",
    [
        "hello",
        "server shutdown",
        "サーバーからの終了理由",
        "a" * 64,
    ],
    ids=["short-ascii", "sso-boundary-ascii", "utf8", "long-ascii"],
)
def test_close_carries_error_code_and_reason(side: str, reason: str) -> None:
    """close() の終了コードと理由が CONNECTION_CLOSE のレコードに載る

    dwnx の `dwnx_ccerr_set_application_error` は理由の文字列をコピーしない
    (dwnx.h に明記されている)。理由の実体は、レコードを書く `pending_record`
    の時点まで生存していなければならない。短い文字列の最適化に収まる長さだけで
    なく、それを超える ASCII と UTF-8 のマルチバイトでも壊れないことを、生成
    されたレコードを解析して確かめる (QMux ではピアが受け取った理由を観測する
    API が無いため、レコードを直接見る)。

    ピアへ届いた後に -224 (DWNX_ERR_DRAINING) が返ることも合わせて確認し、
    レコードとして妥当であることを裏付ける。
    """
    factories = {
        "client": qmux.Connection.create_client,
        "server": qmux.Connection.create_server,
    }
    connection = factories[side](qmux.Config())
    peer = (
        qmux.Connection.create_server(qmux.Config())
        if side == "client"
        else qmux.Connection.create_client(qmux.Config())
    )
    pump(connection, peer)

    connection.close(_CLOSE_ERROR_CODE, reason)
    record = connection.pending_record
    assert record is not None

    close = parse_connection_close(bytes(record))
    assert close is not None, "CONNECTION_CLOSE をレコードから取り出せない"
    assert close.is_application is True
    assert close.frame_type == 0x1D
    assert close.error_code == _CLOSE_ERROR_CODE
    assert close.reason == reason

    # ピアは CONNECTION_CLOSE を処理して draining へ遷移する
    assert peer.receive(record) == DWNX_ERR_DRAINING


@pytest.mark.parametrize(
    ("error_code", "expected_reason_length"),
    [
        (_CLOSE_ERROR_CODE, 16378),
        (2**62 - 1, 16371),
    ],
    ids=["1-byte-code", "8-byte-code"],
)
def test_close_truncates_reason_to_fit_one_record(
    error_code: int, expected_reason_length: int
) -> None:
    """1 レコードに収まらない理由は、収まる長さへ切り詰めて送る

    QMux は 1 つのフレームが 1 つのレコードに収まることを要求する
    (draft-ietf-quic-qmux-02 Section 3.2)。dwnx はレコードの Frames フィールドを
    16382 バイト (DWNX_DEFAULT_MAX_RECORD_SIZE) に制限するため、理由が長すぎると
    CONNECTION_CLOSE のレコードそのものを作れず、終了コードもピアへ届かない。
    終了コードを必ず届けるため、理由を収まる長さへ切り詰める。

    切り詰めた後のレコードは 16384 バイト (レコード長 2 バイト + Frames 16382
    バイト) ちょうどになり、理由に使えるのはフレームの種別 (1 バイト) と終了
    コード (varint) と理由の長さ (varint) を差し引いた残りである。終了コードが
    8 バイトの varint になる場合は、その分だけ理由が短くなる。
    """
    connection = qmux.Connection.create_server(qmux.Config())
    peer = qmux.Connection.create_client(qmux.Config())
    pump(connection, peer)

    connection.close(error_code, "a" * 20000)
    record = connection.pending_record
    assert record is not None

    assert len(record) == _MAX_RECORD_SIZE
    close = parse_connection_close(bytes(record))
    assert close is not None
    assert close.error_code == error_code
    assert close.reason == "a" * expected_reason_length

    # 切り詰めてもレコードとしては妥当で、ピアは draining へ遷移する
    assert peer.receive(record) == DWNX_ERR_DRAINING


@pytest.mark.parametrize(
    ("reason", "expected_bytes"),
    [
        ("a", 16378),
        ("é", 16378),
        ("あ", 16377),
        ("😀", 16376),
    ],
    ids=["ascii", "2-byte", "3-byte", "4-byte"],
)
def test_close_truncates_reason_at_utf8_boundary(reason: str, expected_bytes: int) -> None:
    """切り詰めはマルチバイト文字の途中で切らない

    RFC 9000 Section 19.19 は理由が UTF-8 であることを推奨する。切り詰めの位置が
    文字の途中に落ちる場合は、その文字の先頭まで戻す。末尾がリードバイトで
    終わる場合 (後続バイトが欠けている) も切れているとみなす。

    1 文字のバイト数によって切り詰め後の長さが変わることを、ASCII (1 バイト)・
    é (2 バイト)・あ (3 バイト)・😀 (4 バイト) で確かめる。上限の 16378 バイトは
    ASCII と é ではちょうど文字の区切りになるが、あ では 5460 文字目の 1 バイト目
    (リードバイト) で終わり、😀 では 2 バイト目で終わるため、1 文字分戻る。
    """
    connection = qmux.Connection.create_server(qmux.Config())
    connection.close(_CLOSE_ERROR_CODE, reason * 20000)
    record = connection.pending_record
    assert record is not None

    close = parse_connection_close(bytes(record))
    assert close is not None
    assert close.error_code == _CLOSE_ERROR_CODE
    assert len(close.reason.encode()) == expected_bytes
    # 文字が壊れていないこと (元の文字列の接頭辞になっていること)
    assert close.reason == (reason * 20000)[: len(close.reason)]
    assert len(record) <= _MAX_RECORD_SIZE


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda connection: connection.close(_MAX_VARINT + 1, ""), id="close"),
        pytest.param(
            lambda connection: connection.reset_stream(0, _MAX_VARINT + 1),
            id="reset_stream",
        ),
        pytest.param(
            lambda connection: connection.stop_sending(0, _MAX_VARINT + 1),
            id="stop_sending",
        ),
        pytest.param(
            lambda connection: connection.close_stream(0, _MAX_VARINT + 1),
            id="close_stream",
        ),
    ],
)
def test_invalid_error_code_raises_value_error(
    call: Callable[[qmux.Connection], None],
) -> None:
    """QUIC varint の上限を超える終了コードが ValueError になる

    dwnx は終了コードを QUIC varint として符号化する際に assert で上限を検査し、
    超える値ではプロセスが abort する (SIGABRT)。ストリームの有無や接続の状態に
    よらず dwnx を呼ぶ前に弾き、Python の例外にする。呼び出しはラムダで渡すため、
    終了コードを受け取る 4 つの経路を同じ検査で覆える。
    """
    connection = qmux.Connection.create_client(qmux.Config())
    with pytest.raises(ValueError):
        call(connection)


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
