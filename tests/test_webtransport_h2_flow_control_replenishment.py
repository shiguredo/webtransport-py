"""WebTransport over HTTP/2 のフロー制御クレジット補充テスト

draft-15 Section 4.4 の SHOULD「受信消費に応じたクレジット送出」に従い、
WT_MAX_DATA / WT_MAX_STREAM_DATA を受信量の 1/2 到達で補充し、
WT_MAX_STREAMS をストリーム終了時に補充することを検証する。送信側は
超過時にセッションを閉じず BLOCKED 送出と保留キューで待ち、対向の
MAX 受信で送出を再開する。両ハーフ終端したストリームエントリの解放も
検証する (実セッションを使う。モックなし)。
"""

from __future__ import annotations

from conftest import (
    _connect_h2_session,
    _create_h2_session_pair,
    _drain_events,
    _encode_capsule,
    _encode_data_frame,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2

_WT_MAX_DATA = 0x190B4D3D
_WT_MAX_STREAM_DATA = 0x190B4D3E
_WT_STREAM_DATA_BLOCKED = 0x190B4D42
_WT_DATA_BLOCKED = 0x190B4D41


def _pump_all(client: h2.Session, server: h2.Session, rounds: int = 8) -> None:
    """両方向の送受信が落ち着くまで繰り返す"""
    for _ in range(rounds):
        _h2_pump(client, server)
        _h2_pump(server, client)


def _received_stream_bytes(session: h2.Session) -> int:
    """STREAM_DATA イベントの合計バイト数を返す"""
    return sum(
        len(event.data)
        for event in _drain_events(session)
        if event.type == h2.EventType.STREAM_DATA
    )


def test_session_transfer_beyond_1mib() -> None:
    """1 MiB を超えるセッション転送が自己クローズせずに完了する"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # 既定の wt_initial_max_data (1 MiB) を超える 17 * 64 KiB を送る
    chunk = b"x" * 65536
    for _ in range(17):
        client.send_stream_data(session_id, stream_id, chunk, False)
        _pump_all(client, server)

    assert client.get_session_ids() == [session_id]
    assert _received_stream_bytes(server) == 17 * 65536


def test_single_stream_transfer_beyond_256kib() -> None:
    """256 KiB を超える単一ストリーム転送が自己クローズせずに完了する"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # 既定の wt_initial_max_stream_data (256 KiB) を 1 バイト超える
    chunk = b"x" * 65536
    for _ in range(4):
        client.send_stream_data(session_id, stream_id, chunk, False)
        _pump_all(client, server)
    client.send_stream_data(session_id, stream_id, b"y", False)
    _pump_all(client, server)

    assert client.get_session_ids() == [session_id]
    assert _received_stream_bytes(server) == 4 * 65536 + 1


def test_101st_stream_after_close() -> None:
    """100 本を両方向 FIN で閉じた後に 101 本目を開ける"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    for _ in range(100):
        stream_id = client.open_stream(session_id, False)
        assert stream_id >= 0
        client.send_stream_data(session_id, stream_id, b"hi", True)
        _pump_all(client, server)
        # サーバー側も FIN を返して両ハーフ終端にする
        server.send_stream_data(session_id, stream_id, b"bye", True)
        _pump_all(client, server)

    assert client.get_session_ids() == [session_id]
    stream_101 = client.open_stream(session_id, False)
    assert stream_101 >= 0, "100 本終了後に 101 本目を開けません"


def test_both_half_terminated_entry_released() -> None:
    """両ハーフ終端したストリームエントリが解放される"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    assert stream_id in client.get_stream_ids(session_id)

    # 片方向 FIN のみでは解放されない
    client.send_stream_data(session_id, stream_id, b"hi", True)
    _pump_all(client, server)
    assert stream_id in client.get_stream_ids(session_id)

    # 両方向 FIN で解放される
    server.send_stream_data(session_id, stream_id, b"bye", True)
    _pump_all(client, server)
    assert stream_id not in client.get_stream_ids(session_id)
    assert stream_id not in server.get_stream_ids(session_id)


def test_blocked_sent_once_and_resume() -> None:
    """超過試行の初回のみ BLOCKED を送出し MAX 受信で再開する"""
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_initial_max_stream_data = 4
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # クレジット 4 バイトを超える 6 バイト送信は残量分を送出して残りを保留する
    client.send_stream_data(session_id, stream_id, b"012345")
    assert client.get_session_ids() == [session_id]
    wire = client.send()
    assert wire is not None
    blocked = _encode_capsule(
        _WT_STREAM_DATA_BLOCKED, _encode_varint(stream_id) + _encode_varint(4)
    )
    assert blocked in wire
    # 重複送出しない (再試行しても 1 個のまま)
    client.send_stream_data(session_id, stream_id, b"67")
    wire2 = client.send()
    assert wire2 is None or blocked not in wire2

    # 先行分を配送し、対向の付与で保留分の送出が再開される
    server.receive(wire)
    client.receive(
        _encode_data_frame(
            session_id,
            _encode_capsule(_WT_MAX_STREAM_DATA, _encode_varint(stream_id) + _encode_varint(1024)),
        )
    )
    _h2_pump(client, server)
    received = b"".join(
        event.data for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    )
    assert received == b"01234567"


def test_max_data_replenished_on_wire() -> None:
    """受信消費に応じて WT_MAX_DATA がワイヤへ送出される"""
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_initial_max_data = 100
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # 受信上限 100 の半分を超える 60 バイトを送る
    client.send_stream_data(session_id, stream_id, b"x" * 60, False)
    _h2_pump(client, server)

    # サーバーは 60 + 100 = 160 の WT_MAX_DATA を送出している
    wire = server.send()
    assert wire is not None
    assert _encode_capsule(_WT_MAX_DATA, _encode_varint(160)) in wire


def test_reset_discards_pending() -> None:
    """リセット後は保留を送出しない"""
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_initial_max_stream_data = 4
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # 超過送信で保留を作り、その後リセットする
    client.send_stream_data(session_id, stream_id, b"012345")
    client.reset_stream(session_id, stream_id, 0)
    # 対向の付与が届いても保留は送出されない
    client.receive(
        _encode_data_frame(
            session_id,
            _encode_capsule(_WT_MAX_STREAM_DATA, _encode_varint(stream_id) + _encode_varint(1024)),
        )
    )
    _h2_pump(client, server)
    received = b"".join(
        event.data for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    )
    # 先行分の 4 バイトのみ届き、保留分は破棄される
    assert received == b"0123"


def test_fin_after_send_ignored() -> None:
    """FIN 保留後の新規送信は無視される"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # FIN 付き送信で送信側を終端させる
    client.send_stream_data(session_id, stream_id, b"hi", True)
    # FIN 後の送信は無視される
    client.send_stream_data(session_id, stream_id, b"after", False)
    _pump_all(client, server)
    received = b"".join(
        event.data for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    )
    assert received == b"hi"


def test_data_blocked_on_wire() -> None:
    """セッション上限超過で WT_DATA_BLOCKED が送出される"""
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_initial_max_data = 8
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # セッション上限 8 バイトを超える送信で塞がる
    client.send_stream_data(session_id, stream_id, b"01234567")
    client.send_stream_data(session_id, stream_id, b"89")
    assert client.get_session_ids() == [session_id]
    wire = client.send()
    assert wire is not None
    assert _encode_capsule(_WT_DATA_BLOCKED, _encode_varint(8)) in wire


def test_streams_blocked_on_wire() -> None:
    """ストリーム数上限超過で WT_STREAMS_BLOCKED が送出される"""
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_initial_max_streams_bidi = 0
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    session_id = _connect_h2_session(client, server)

    # 上限 0 のため開けない
    assert client.open_stream(session_id, False) == -1
    wire = client.send()
    assert wire is not None
    assert _encode_capsule(0x190B4D43, _encode_varint(0)) in wire
    # 重複送出しない
    assert client.open_stream(session_id, False) == -1
    wire2 = client.send()
    assert wire2 is None or _encode_capsule(0x190B4D43, _encode_varint(0)) not in wire2


def test_uni_reset_releases_and_replenishes() -> None:
    """対向開始単方向のリセット受信で解放と補充が行われる"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアントが単方向ストリームを開いてサーバーへ送る
    stream_id = client.open_stream(session_id, True)
    assert stream_id >= 0
    client.send_stream_data(session_id, stream_id, b"hi", False)
    _pump_all(client, server)
    assert stream_id in server.get_stream_ids(session_id)

    # サーバー側でリセットを受信させる (WT_RESET_STREAM を注入する。
    # Reliable Size は受信済み 2 バイトと一致させる)
    reset_payload = _encode_varint(stream_id) + _encode_varint(0) + _encode_varint(2)
    server.receive(_encode_data_frame(session_id, _encode_capsule(0x190B4D39, reset_payload)))
    _pump_all(client, server)
    # 受信終端のみで解放され、WT_MAX_STREAMS_UNI が補充される
    assert stream_id not in server.get_stream_ids(session_id)


def test_uni_release_replenishes_max_streams_on_wire() -> None:
    """単方向ストリーム解放で WT_MAX_STREAMS_UNI が補充される"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    stream_id = client.open_stream(session_id, True)
    assert stream_id >= 0
    client.send_stream_data(session_id, stream_id, b"hi", False)
    _pump_all(client, server)

    # 対向へ WT_RESET_STREAM を注入して受信終端させる (Reliable Size は
    # 受信済み 2 バイトと一致させる)
    reset_payload = _encode_varint(stream_id) + _encode_varint(0) + _encode_varint(2)
    server.receive(_encode_data_frame(session_id, _encode_capsule(0x190B4D39, reset_payload)))
    assert stream_id not in server.get_stream_ids(session_id)

    # WT_MAX_STREAMS_UNI (0x190B4D40) が 101 で送出される
    wire = server.send()
    assert wire is not None
    assert _encode_capsule(0x190B4D40, _encode_varint(101)) in wire


def test_reverse_initiator_reset_does_not_replenish() -> None:
    """逆 initiator ID のリセット受信で補充が水増しされない"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # サーバー開始単方向 ID (3) へ WT_RESET_STREAM を注入する (本来は
    # サーバーが開く ID であり、クライアント起点としては不正に近い)
    reset_payload = _encode_varint(3) + _encode_varint(0) + _encode_varint(0)
    server.receive(_encode_data_frame(session_id, _encode_capsule(0x190B4D39, reset_payload)))
    # エントリは作られても自起点扱いのため解放されず、補充も送出されない
    wire = server.send()
    assert wire is None or _encode_capsule(0x190B4D40, _encode_varint(101)) not in wire


def test_partial_fin_resume() -> None:
    """部分送出の FIN は残りに引き継がれて再開する"""
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_initial_max_stream_data = 4
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # クレジット 4 バイトに対して FIN 付き 6 バイト送信は先行 4 バイトを
    # 送出して残り 2 バイト + FIN を保留する
    client.send_stream_data(session_id, stream_id, b"012345", True)
    assert client.get_session_ids() == [session_id]
    # FIN 保留中の追送は無視される
    client.send_stream_data(session_id, stream_id, b"67", False)

    # 先行分を配送し、対向の付与で残りの送出が再開される
    wire = client.send()
    assert wire is not None
    server.receive(wire)
    client.receive(
        _encode_data_frame(
            session_id,
            _encode_capsule(_WT_MAX_STREAM_DATA, _encode_varint(stream_id) + _encode_varint(1024)),
        )
    )
    _h2_pump(client, server)
    stream_events = [e for e in _drain_events(server) if e.type == h2.EventType.STREAM_DATA]
    assert b"".join(e.data for e in stream_events) == b"012345"
    assert stream_events[-1].fin is True
