"""WebTransport over HTTP/2 の未完成カプセルバッファ上限テスト

Length 解釈直後に単一カプセルのペイロード上限 (既定 1 MiB) を検査し、
超過時は WT_ERROR (0x52) でセッションを閉じることを検証する
(draft-ietf-webtrans-http2-15)。ペイロード待ちの無制限蓄積による
メモリ DoS 経路を塞ぐ。ワイヤ注入で再現する (公開 API では非準拠な
Length を送出する手段が存在しないため)。
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

_WT_STREAM = 0x190B4D3C
_WT_CLOSE_SESSION = 0x2843
_WT_ERROR = 0x52


def _drain_wire(source: h2.Session) -> bytes:
    """送信バッファを全て取り出す"""
    wire = b""
    while True:
        part = source.send()
        if part is None:
            break
        wire += part
    return wire


def test_huge_length_header_closes_with_wt_error() -> None:
    """Length = 2^30 のヘッダー受信で WT_ERROR になることを確認

    ペイロードを送らずヘッダーのみで即座に閉じる (ペイロード待ちで
    蓄積しない)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # Length = 2^30 の WT_STREAM ヘッダーのみ注入する
    evil = _encode_varint(_WT_STREAM) + _encode_varint(1 << 30)
    server.receive(_encode_data_frame(session_id, evil))

    # Error イベント (0x52) が発火し、セッションが閉じる
    error_events = [e for e in _drain_events(server) if e.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR
    assert server.get_session_ids() == []

    # WT_CLOSE_SESSION (0x52) がワイヤへ送出される
    wire = _drain_wire(server)
    expected = _encode_capsule(
        _WT_CLOSE_SESSION, (0x52).to_bytes(4, "big") + b"capsule payload exceeds limit"
    )
    assert expected in wire


def test_huge_length_then_more_data_stays_closed() -> None:
    """長大 Length 後に大量データを送っても蓄積しないことを確認

    閉鎖後に 64 KiB を送ってもクラッシュせず、セッションは閉じたままに
    なる (DoS 耐性の回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    evil = _encode_varint(_WT_STREAM) + _encode_varint(1 << 30)
    server.receive(_encode_data_frame(session_id, evil))
    _drain_events(server)
    assert server.get_session_ids() == []

    # 閉鎖後に大量データを送っても何も起きない
    server.receive(_encode_data_frame(session_id, b"x" * 65536))
    assert server.get_session_ids() == []
    assert [e for e in _drain_events(server) if e.type == h2.EventType.STREAM_DATA] == []


def test_boundary_payload_accepted() -> None:
    """上限ちょうどのペイロードは受理されることを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ちょうど 1 MiB の DATAGRAM ペイロードは受理される (HTTP/2 の
    # フレーム上限に合わせて 16 KiB ずつ分割配送し、断片再構成させる)
    payload = b"y" * 1048576
    capsule = _encode_capsule(0x00, payload)
    for offset in range(0, len(capsule), 16384):
        server.receive(_encode_data_frame(session_id, capsule[offset : offset + 16384]))
    datagram_events = [e for e in _drain_events(server) if e.type == h2.EventType.DATAGRAM]
    assert len(datagram_events) == 1
    assert datagram_events[0].data == payload
    assert server.get_session_ids() == [session_id]


def test_custom_limit_applies() -> None:
    """Config の上限値が適用されることを確認"""
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_max_capsule_payload_size = 100
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    session_id = _connect_h2_session(client, server)

    # 101 バイトの Length で閉じる
    evil = _encode_varint(_WT_STREAM) + _encode_varint(101)
    server.receive(_encode_data_frame(session_id, evil))
    error_events = [e for e in _drain_events(server) if e.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR
    assert server.get_session_ids() == []


def test_split_transfer_unaffected() -> None:
    """複数カプセルに分割した転送が影響を受けないことを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # 1 MiB を小片に分割して送る (カプセル単位では上限以下)。保留分の
    # 排出のため送受信を繰り返す (フロー制御の往復が必要なため多めに回す)
    chunk = b"z" * 32768
    for _ in range(32):
        client.send_stream_data(session_id, stream_id, chunk, False)
    for _ in range(100):
        _h2_pump(client, server)
        _h2_pump(server, client)

    assert client.get_session_ids() == [session_id]
    assert server.get_session_ids() == [session_id]
    total = sum(len(e.data) for e in _drain_events(server) if e.type == h2.EventType.STREAM_DATA)
    assert total == 32 * 32768


def test_custom_limit_boundary_accepted() -> None:
    """カスタム上限ちょうどは受理されることを確認"""
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_max_capsule_payload_size = 100
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    session_id = _connect_h2_session(client, server)

    # 100 バイトの DATAGRAM ペイロードは受理される
    payload = b"q" * 100
    server.receive(_encode_data_frame(session_id, _encode_capsule(0x00, payload)))
    datagram_events = [e for e in _drain_events(server) if e.type == h2.EventType.DATAGRAM]
    assert len(datagram_events) == 1
    assert datagram_events[0].data == payload
    assert server.get_session_ids() == [session_id]


def test_pre_accept_huge_length_errors_on_drain() -> None:
    """受理前の長大 Length が排出時に WT_ERROR になることを確認

    受理前は総量上限のみ適用され、Length 検査は受理後の排出時に適用
    される (時間的排他)。上限内のヘッダーバイト列として蓄積され、
    accept 後に WT_ERROR で閉じる。
    """
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_pre_accept_buffer_limit = 65536
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    wire_parts = []
    while True:
        part = client.send()
        if part is None:
            break
        wire_parts.append(part)
    assert wire_parts
    server.receive(b"".join(wire_parts))

    # 長大 Length ヘッダー (小バイト列) を受理前に蓄積させる
    evil = _encode_varint(_WT_STREAM) + _encode_varint(1 << 30)
    server.receive(_encode_data_frame(session_id, evil))
    # 蓄積時点では閉じない (総量上限内)
    assert server.get_session_ids() == []
    assert not [e for e in _drain_events(server) if e.type == h2.EventType.ERROR]

    # 受理後の排出で WT_ERROR になる
    assert server.accept_session(session_id) is True
    error_events = [e for e in _drain_events(server) if e.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == _WT_ERROR


def test_trailing_capsule_blocked_in_same_receive() -> None:
    """同一受信内の後続カプセルが遮断されることを確認

    長大 Length ヘッダーと正常 DATAGRAM を同一 DATA で連結し、後続が
    処理されないことを検証する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    evil = _encode_varint(_WT_STREAM) + _encode_varint(1 << 30)
    good = _encode_capsule(0x00, b"should-not-arrive")
    server.receive(_encode_data_frame(session_id, evil + good))
    assert server.get_session_ids() == []
    assert [e for e in _drain_events(server) if e.type == h2.EventType.DATAGRAM] == []
