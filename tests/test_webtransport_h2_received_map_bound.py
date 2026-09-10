"""WebTransport over HTTP/2 の受信マップ上限テスト

受信系コンテナ (received_max_stream_data_by_id /
received_stop_sending_stream_ids) がピアの入力で無制限に増えるメモリ
DoS への防御 (固定上限の安全弁) を検証する。上限超過の新規 ID は
保持せず、上限内の二重受信検出・減少検出と、実在ストリームへの
イベント通知・クレジット反映は維持される。
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
_WT_STOP_SENDING = 0x190B4D3A
_WT_MAX_STREAM_DATA = 0x190B4D3E
_WT_STREAM_STATE_ERROR = 0x51

# 1 つの DATA フレームにまとめるカプセル数。カプセルごとに receive を
# 呼ぶと Python → C++ 呼び出しが増えて遅いためバッチ注入する
_BATCH_SIZE = 1000
# 未知 ID の大量投入数。安全弁の上限 (4096) を大きく超える
_BULK_IDS = 300_000
# 安全弁の上限 (src/bindings/webtransport_h2.cpp の kMaxReceivedMapEntries)
_MAP_LIMIT = 4096


def _inject_batch(server: h2.Session, session_id: int, capsules: list[bytes]) -> None:
    """複数カプセルを 1 つの DATA フレームにまとめてワイヤ注入する"""
    assert server.receive(_encode_data_frame(session_id, b"".join(capsules))) > 0


def _max_stream_data_capsule(stream_id: int, max_data: int) -> bytes:
    """WT_MAX_STREAM_DATA カプセルを構築する"""
    return _encode_capsule(
        _WT_MAX_STREAM_DATA, _encode_varint(stream_id) + _encode_varint(max_data)
    )


def _stop_sending_capsule(stream_id: int) -> bytes:
    """WT_STOP_SENDING カプセルを構築する"""
    return _encode_capsule(_WT_STOP_SENDING, _encode_varint(stream_id) + _encode_varint(0))


def _error_events(server: h2.Session) -> list:
    """ERROR イベントを取り出す"""
    return [e for e in _drain_events(server) if e.type == h2.EventType.ERROR]


def _stream_state_errors(server: h2.Session) -> list:
    """WT_STREAM_STATE_ERROR イベントを取り出す"""
    return [
        e
        for e in _drain_events(server)
        if e.type == h2.EventType.ERROR and e.error_code == _WT_STREAM_STATE_ERROR
    ]


def _drain_wire(server: h2.Session) -> None:
    """サーバーの送信バッファを読み捨てる"""
    while server.send() is not None:
        pass


def _fill_unknown_max_stream_data(
    server: h2.Session, session_id: int, count: int, first_index: int = 1
) -> None:
    """未知のピア起点 bidi ID へ WT_MAX_STREAM_DATA を count 件注入する"""
    for batch_start in range(0, count, _BATCH_SIZE):
        batch_count = min(_BATCH_SIZE, count - batch_start)
        capsules = [
            _max_stream_data_capsule((first_index + batch_start + offset) * 4, 1 << 20)
            for offset in range(batch_count)
        ]
        _inject_batch(server, session_id, capsules)
        _drain_wire(server)


def _fill_unknown_stop_sending(server: h2.Session, session_id: int, count: int) -> None:
    """未知のピア起点 bidi ID へ WT_STOP_SENDING を count 件注入する"""
    for batch_start in range(0, count, _BATCH_SIZE):
        batch_count = min(_BATCH_SIZE, count - batch_start)
        capsules = [
            _stop_sending_capsule((batch_start + offset + 1) * 4) for offset in range(batch_count)
        ]
        _inject_batch(server, session_id, capsules)
        _drain_wire(server)


def test_bulk_unknown_max_stream_data_keeps_session_alive() -> None:
    """未知 ID 30 万個の WT_MAX_STREAM_DATA 投入後もセッションが生存する

    安全弁の上限を超えた分は保持しないだけで、受信自体は受け付け続け、
    セッションを閉じないことを確認する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアント起点 bidi のストリームを 1 本作り、既存ストリームにする
    stream_id = client.open_stream(session_id, False)
    assert stream_id == 0
    client.send_stream_data(session_id, stream_id, b"hi", False)
    _h2_pump(client, server)
    _drain_events(server)

    # 未知 ID へ大量注入する。初期クレジット (262144) より大きい値に
    # しないと減少検出でセッションエラーになる
    for batch_start in range(0, _BULK_IDS, _BATCH_SIZE):
        count = min(_BATCH_SIZE, _BULK_IDS - batch_start)
        capsules = [
            _max_stream_data_capsule((batch_start + offset + 1) * 4, 1 << 20)
            for offset in range(count)
        ]
        _inject_batch(server, session_id, capsules)
        _drain_wire(server)

    # セッションは生存し、エラーイベントは出ない
    assert server.get_session_ids() == [session_id]
    assert not _error_events(server)

    # 上限到達後も既存ストリームへの WT_MAX_STREAM_DATA は受け付ける
    _inject_batch(server, session_id, [_max_stream_data_capsule(stream_id, 1 << 21)])
    assert not _error_events(server)
    assert server.get_session_ids() == [session_id]


def test_max_stream_data_over_limit_new_id_not_retained() -> None:
    """上限超過の新規 ID への WT_MAX_STREAM_DATA は保持されない

    保持されないため、その ID への減少値は検出されずセッションが生存
    する (上限超過分は減少検出 (draft-15 Section 6.6) の対象外になる
    既知の制約)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # 未知 ID で上限まで充填する
    _fill_unknown_max_stream_data(server, session_id, _MAP_LIMIT)
    assert not _error_events(server)
    assert server.get_session_ids() == [session_id]

    # 上限超過の新規 ID は保持されず、減少値を送っても
    # WT_FLOW_CONTROL_ERROR の WT_CLOSE_SESSION (Type 0x2843) は送出されない
    over_id = (_MAP_LIMIT + 100) * 4
    _inject_batch(server, session_id, [_max_stream_data_capsule(over_id, 1 << 20)])
    _inject_batch(server, session_id, [_max_stream_data_capsule(over_id, 1 << 19)])
    assert not _error_events(server)
    wire = server.send()
    assert wire is None or b"\x68\x43" not in wire
    assert server.get_session_ids() == [session_id]


def test_max_stream_data_existing_stream_decrease_detected_after_limit() -> None:
    """上限到達後も実在ストリームの減少検出は維持される

    実在ストリームは max_stream_data_local を前回値に使うため、上限
    超過でも減少値が WT_FLOW_CONTROL_ERROR になる (クレジット反映と
    減少検出を落とさない)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアント起点 bidi の実在ストリームを作る
    stream_id = client.open_stream(session_id, False)
    assert stream_id == 0
    client.send_stream_data(session_id, stream_id, b"hi", False)
    _h2_pump(client, server)
    _drain_events(server)

    # 実在ストリームの ID 0 を避けて未知 ID で上限まで充填する
    _fill_unknown_max_stream_data(server, session_id, _MAP_LIMIT, first_index=1)
    assert not _error_events(server)

    # 実在ストリームへ増加値を送り、その後減少値を送ると
    # WT_FLOW_CONTROL_ERROR の WT_CLOSE_SESSION (Type 0x2843) が送出される
    _inject_batch(server, session_id, [_max_stream_data_capsule(stream_id, 1 << 20)])
    assert not _error_events(server)
    _inject_batch(server, session_id, [_max_stream_data_capsule(stream_id, 1 << 19)])
    wire = server.send()
    assert wire is not None
    assert b"\x68\x43" in wire


def test_stop_sending_within_limit_emits_event() -> None:
    """上限内の未知 ID への WT_STOP_SENDING がイベントとして発火する"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    unknown_id = 4 * 10
    _inject_batch(server, session_id, [_stop_sending_capsule(unknown_id)])

    events = _drain_events(server)
    assert not [e for e in events if e.type == h2.EventType.ERROR]
    stop_events = [e for e in events if e.type == h2.EventType.STOP_SENDING]
    assert len(stop_events) == 1
    assert stop_events[0].stream_id == unknown_id
    assert server.get_session_ids() == [session_id]


def test_stop_sending_over_limit_unknown_ignored() -> None:
    """安全弁の上限超過の未知 ID への WT_STOP_SENDING は無視される

    上限到達後に新しい未知 ID を送ってもイベントは発火せず、セッション
    も閉じない。コンテナを上限で有界にする安全弁の挙動を固定する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # 未知 ID でコンテナを上限まで埋める
    _fill_unknown_stop_sending(server, session_id, _MAP_LIMIT)
    events = _drain_events(server)
    assert len([e for e in events if e.type == h2.EventType.STOP_SENDING]) > 0

    # 上限超過の新しい未知 ID は保持されずイベントも発火しない
    over_id = (_MAP_LIMIT + 100) * 4
    _inject_batch(server, session_id, [_stop_sending_capsule(over_id)])
    events = _drain_events(server)
    assert not [e for e in events if e.type == h2.EventType.STOP_SENDING]
    assert not [e for e in events if e.type == h2.EventType.ERROR]
    assert server.get_session_ids() == [session_id]


def test_stop_sending_for_existing_stream_survives_map_limit() -> None:
    """安全弁の上限到達後も実在ストリームへの WT_STOP_SENDING が通知される

    未知 ID のジャンクでコンテナを埋めても、実在ストリームへの送信停止
    要求はアプリへ届き続ける (アプリが WT_RESET_STREAM 応答を実装できる)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアント起点 bidi の実在ストリームを作る
    stream_id = client.open_stream(session_id, False)
    assert stream_id == 0
    client.send_stream_data(session_id, stream_id, b"hi", False)
    _h2_pump(client, server)
    _drain_events(server)

    # 未知 ID でコンテナを安全弁の上限まで埋める (超過分は保持されない)
    _fill_unknown_stop_sending(server, session_id, _MAP_LIMIT + 10)
    events = _drain_events(server)
    assert not [e for e in events if e.type == h2.EventType.ERROR]
    assert server.get_session_ids() == [session_id]

    # 上限到達後も実在ストリームへの WT_STOP_SENDING は通知される
    _inject_batch(server, session_id, [_stop_sending_capsule(stream_id)])
    events = _drain_events(server)
    stop_events = [e for e in events if e.type == h2.EventType.STOP_SENDING]
    assert len(stop_events) == 1
    assert stop_events[0].stream_id == stream_id
    assert not [e for e in events if e.type == h2.EventType.ERROR]
    assert server.get_session_ids() == [session_id]

    # 2 回目は上限到達後も二重受信としてストリーム状態エラーになる
    # (実在ストリームは WtStreamInfo のフラグで検出する)
    _inject_batch(server, session_id, [_stop_sending_capsule(stream_id)])
    errors = _stream_state_errors(server)
    assert len(errors) == 1


def test_stop_sending_after_release_still_detected_as_duplicate() -> None:
    """解放後の 2 回目 WT_STOP_SENDING もストリーム状態エラーになる

    受信記録はセッション中保持されるため、二重受信検出 (draft-15
    Section 6.3 の MUST) が解放後も維持される。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアント起点 bidi (ID 0) を作る
    stream_id = client.open_stream(session_id, False)
    assert stream_id == 0
    client.send_stream_data(session_id, stream_id, b"hi", False)
    _h2_pump(client, server)
    _drain_events(server)

    # 1 回目の WT_STOP_SENDING はイベントとして通知される
    _inject_batch(server, session_id, [_stop_sending_capsule(stream_id)])
    events = _drain_events(server)
    assert len([e for e in events if e.type == h2.EventType.STOP_SENDING]) == 1

    # 両方向を終端して解放する
    client.send_stream_data(session_id, stream_id, b"", True)
    server.send_stream_data(session_id, stream_id, b"", True)
    _h2_pump(client, server)
    _drain_wire(server)
    _drain_events(server)

    # 解放後の 2 回目は二重受信としてストリーム状態エラーになる
    _inject_batch(server, session_id, [_stop_sending_capsule(stream_id)])
    errors = _stream_state_errors(server)
    assert len(errors) == 1
