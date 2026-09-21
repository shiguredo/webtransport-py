"""WebTransport over HTTP/2 の不完全なカプセルペイロードの検証テスト

カプセルの Length は整合しているが、ペイロードに含まれる可変長整数が完結に
デコードできない場合の挙動を検証する。RFC 9297 Section 3.3 は「カプセルの
ペイロードは識別フィールドを正確に含まなければならず、識別フィールドの終端に
達していないペイロードは malformed / incomplete な HTTP メッセージとして扱う」
ことを MUST としており、HTTP/2 では RFC 9113 Section 8.1.1 により PROTOCOL_ERROR
のストリームエラー (RST_STREAM) を送出する。draft-ietf-webtrans-http2-15
Section 3.4 のとおり、ストリームのリセットはセッションを終了させる。

対象はペイロードの可変長整数を読む 9 ハンドラ (WT_STREAM / WT_RESET_STREAM /
WT_STOP_SENDING / WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS /
WT_STREAM_DATA_BLOCKED / WT_STREAMS_BLOCKED / WT_DATA_BLOCKED) すべてである。
デコード失敗を無言で return していた時期には、方向検証・ストリーム状態検証・
二重受信検証が入力次第で回避できた。WT_DATA_BLOCKED は読み出しハンドラ自体が
無く、PADDING と同じ no-op 分岐でカプセルごと読み捨てられていたため、
Maximum Data の欠落が検証されない状態だった。

Stream ID のみでデータを持たない WT_STREAM は、ストリームを開閉しない
カプセルとして従来どおりイベントを発火しない (draft-15 Section 6.4 の MAY。
WebKit 相互運用のための既存の判断)。ペイロードが空 (Length 0) の WT_STREAM は
Stream ID すら含まれず「識別フィールドの終端に達していない」ため、他の
ハンドラと同じくリセットする。カプセルの Type / Length / ペイロード自体が
途中の場合は、後続のバイト列を待って蓄積する (RFC 9297 Section 3.2)。
"""

from __future__ import annotations

import pytest
from conftest import (
    _PROTOCOL_ERROR,
    _WT_CLOSE_SESSION_TYPE_BYTES,
    _assert_session_closed_by_protocol_error,
    _connect_h2_session,
    _create_h2_session_pair,
    _drain_events,
    _encode_capsule,
    _encode_data_frame,
    _encode_rst_stream_frame,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2

# カプセル種別 (draft-ietf-webtrans-http2-15 Section 6)
_WT_RESET_STREAM = 0x190B4D39
_WT_STOP_SENDING = 0x190B4D3A
_WT_STREAM_FIN = 0x190B4D3B
_WT_STREAM = 0x190B4D3C
_WT_MAX_DATA = 0x190B4D3D
_WT_MAX_STREAM_DATA = 0x190B4D3E
_WT_MAX_STREAMS_BIDI = 0x190B4D3F
_WT_MAX_STREAMS_UNI = 0x190B4D40
_WT_DATA_BLOCKED = 0x190B4D41
_WT_STREAM_DATA_BLOCKED = 0x190B4D42
_WT_STREAMS_BLOCKED_BIDI = 0x190B4D43
_WT_STREAMS_BLOCKED_UNI = 0x190B4D44
_WT_CLOSE_SESSION = 0x2843

# 不完全な可変長整数。0x40 は 2 バイト、0x80 は 4 バイト、0xc0 は 8 バイト
# varint のプレフィックスであり、いずれも 1 バイトしか無いためデコードに失敗する
_INCOMPLETE_VARINT_2BYTE = b"\x40"
_INCOMPLETE_VARINT_4BYTE = b"\x80"
_INCOMPLETE_VARINT_8BYTE = b"\xc0"


@pytest.mark.parametrize(
    ("capsule_type", "payload"),
    [
        # WT_STREAM / WT_STREAM_FIN: Stream ID が不完全 / ペイロードが空。
        # ペイロードのデコードは両 Type で同じ経路だが、FIN 付き Type の
        # ディスパッチ (0x190B4D3B) も固定するため両方を並べる
        (_WT_STREAM, _INCOMPLETE_VARINT_2BYTE),
        (_WT_STREAM_FIN, _INCOMPLETE_VARINT_2BYTE),
        (_WT_STREAM, b""),
        (_WT_STREAM_FIN, b""),
        # WT_RESET_STREAM: Stream ID / Error Code / Reliable Size が不完全、
        # および空ペイロード (Length 0 の分岐)
        (_WT_RESET_STREAM, _INCOMPLETE_VARINT_2BYTE),
        (_WT_RESET_STREAM, _encode_varint(0) + _INCOMPLETE_VARINT_2BYTE),
        (_WT_RESET_STREAM, _encode_varint(0) + _encode_varint(42) + _INCOMPLETE_VARINT_2BYTE),
        (_WT_RESET_STREAM, b""),
        # WT_STOP_SENDING: Stream ID / Error Code が不完全、および空ペイロード
        (_WT_STOP_SENDING, _INCOMPLETE_VARINT_2BYTE),
        (_WT_STOP_SENDING, _encode_varint(0) + _INCOMPLETE_VARINT_2BYTE),
        (_WT_STOP_SENDING, b""),
        # WT_MAX_DATA: Maximum Data が不完全 (2 バイト / 4 バイト varint)
        (_WT_MAX_DATA, _INCOMPLETE_VARINT_2BYTE),
        (_WT_MAX_DATA, _INCOMPLETE_VARINT_4BYTE),
        (_WT_MAX_DATA, _INCOMPLETE_VARINT_8BYTE),
        # WT_MAX_STREAM_DATA: Stream ID / Maximum Stream Data が不完全
        (_WT_MAX_STREAM_DATA, _INCOMPLETE_VARINT_2BYTE),
        (_WT_MAX_STREAM_DATA, _encode_varint(0) + _INCOMPLETE_VARINT_2BYTE),
        # WT_MAX_STREAMS: 両方向 / 単方向
        (_WT_MAX_STREAMS_BIDI, _INCOMPLETE_VARINT_2BYTE),
        (_WT_MAX_STREAMS_UNI, _INCOMPLETE_VARINT_2BYTE),
        # WT_DATA_BLOCKED: Maximum Data が不完全 (2 バイト / 4 バイト / 8 バイト
        # varint)、および空ペイロード
        (_WT_DATA_BLOCKED, _INCOMPLETE_VARINT_2BYTE),
        (_WT_DATA_BLOCKED, _INCOMPLETE_VARINT_4BYTE),
        (_WT_DATA_BLOCKED, _INCOMPLETE_VARINT_8BYTE),
        (_WT_DATA_BLOCKED, b""),
        # WT_STREAM_DATA_BLOCKED: Stream ID / Maximum Stream Data が不完全
        (_WT_STREAM_DATA_BLOCKED, _INCOMPLETE_VARINT_2BYTE),
        (_WT_STREAM_DATA_BLOCKED, _encode_varint(0) + _INCOMPLETE_VARINT_2BYTE),
        # WT_STREAMS_BLOCKED: 両方向 / 単方向
        (_WT_STREAMS_BLOCKED_BIDI, _INCOMPLETE_VARINT_2BYTE),
        (_WT_STREAMS_BLOCKED_UNI, _INCOMPLETE_VARINT_2BYTE),
    ],
    ids=[
        "stream_id",
        "stream_fin_id",
        "stream_empty",
        "stream_fin_empty",
        "reset_stream_id",
        "reset_stream_error_code",
        "reset_stream_reliable_size",
        "reset_stream_empty",
        "stop_sending_stream_id",
        "stop_sending_error_code",
        "stop_sending_empty",
        "max_data_2byte",
        "max_data_4byte",
        "max_data_8byte",
        "max_stream_data_stream_id",
        "max_stream_data_value",
        "max_streams_bidi",
        "max_streams_uni",
        "data_blocked_2byte",
        "data_blocked_4byte",
        "data_blocked_8byte",
        "data_blocked_empty",
        "stream_data_blocked_stream_id",
        "stream_data_blocked_value",
        "streams_blocked_bidi",
        "streams_blocked_uni",
    ],
)
def test_incomplete_payload_resets_stream(capsule_type: int, payload: bytes) -> None:
    """不完全なペイロードのカプセルが PROTOCOL_ERROR でストリームを閉じることを確認

    デコード失敗を無言で return していた時期はセッションが維持されたまま
    検証が回避でき、WT_DATA_BLOCKED はペイロードを読むハンドラ自体が無く
    カプセルごと読み捨てられていた。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    assert server.get_session_ids() == [session_id], "サーバー側のセッションが確立していません"

    ret = server.receive(_encode_data_frame(session_id, _encode_capsule(capsule_type, payload)))
    assert ret > 0, "カプセルの注入に失敗しました"

    _assert_session_closed_by_protocol_error(server, client, session_id)


@pytest.mark.parametrize(
    "split_at",
    [1, 2, 3],
    ids=["type_middle", "length_middle", "payload_partial"],
)
def test_incomplete_capsule_framing_waits_for_more_data(split_at: int) -> None:
    """カプセルのフレーミングが途中の場合は蓄積して後続を待つことを確認

    RFC 9297 Section 3.2 の Capsule Protocol は Type / Length / Value の
    連続であり、フレーミングの途中ではエラーにせず後続のバイト列を待つ。
    Type の途中 (先頭 1 バイト)・Length の途中・ペイロード途中のいずれでも、
    分割中はリセットせず、残りが届いた時点でカプセルが処理される
    (「ペイロードが不完全なカプセルはリセットする」との取り違えの回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # Error Code 0 (4 バイト) + メッセージ無しの WT_CLOSE_SESSION
    capsule = _encode_capsule(_WT_CLOSE_SESSION, (0).to_bytes(4, "big"))
    assert capsule.startswith(_WT_CLOSE_SESSION_TYPE_BYTES)
    assert split_at < len(capsule), "カプセルが分割位置より短いです"

    # 前半だけを渡す (Type / Length / Value のいずれかの途中)
    ret = server.receive(_encode_data_frame(session_id, capsule[:split_at]))
    assert ret > 0, "カプセルの注入に失敗しました"
    events = _drain_events(server)
    assert all(event.type != h2.EventType.ERROR for event in events), "Error イベントが発火しました"
    assert server.get_session_ids() == [session_id], "セッションが終了しました"
    wire = server.send()
    assert wire is None or _encode_rst_stream_frame(session_id, _PROTOCOL_ERROR) not in wire, (
        "分割中に RST_STREAM が送出されました"
    )

    # 残りを渡すとカプセルが処理され、セッションが終了する
    ret = server.receive(_encode_data_frame(session_id, capsule[split_at:]))
    assert ret > 0, "カプセルの残りの注入に失敗しました"
    closed_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1, "SessionClosed が 1 件だけ発火していません"
    assert closed_events[0].session_id == session_id
    assert server.get_session_ids() == []


def test_wt_stream_without_data_creates_stream_without_events() -> None:
    """Stream ID のみの WT_STREAM がイベントを発火せずストリームを作成することを確認

    Stream ID は完全にデコードできるため不完全なペイロードの扱いの対象外で
    あり、データも FIN も持たないカプセルはストリームを開閉しない (draft-15
    Section 6.4 の MAY の対象)。実装はストリームを暗黙作成するが、イベントは
    発火せずセッションも維持される (WebKit 相互運用のための既存の判断)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(
        _encode_data_frame(session_id, _encode_capsule(_WT_STREAM, _encode_varint(0)))
    )
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"

    events = _drain_events(server)
    assert all(event.type != h2.EventType.ERROR for event in events), "Error イベントが発火しました"
    assert server.get_session_ids() == [session_id], "セッションが終了しました"
    assert server.get_stream_ids(session_id) == [0], "ストリームが暗黙作成されていません"
    wire = server.send()
    assert wire is None or _encode_rst_stream_frame(session_id, _PROTOCOL_ERROR) not in wire


def test_incomplete_payload_in_pre_accept_buffer_resets_on_accept() -> None:
    """受理前に蓄積した不完全なカプセルが accept_session の排出でリセットされることを確認

    サーバーは受理前のカプセルを蓄積し、accept_session で遅延処理する
    (draft-15 Section 3.2 の楽観的送信)。この経路でも不完全なペイロードは
    PROTOCOL_ERROR の RST_STREAM になり、初期クレジット (WT_MAX_DATA 等) は
    送出されない。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # 受理前に不完全なペイロードのカプセルを届ける (この時点では処理されない)
    ret = server.receive(
        _encode_data_frame(session_id, _encode_capsule(_WT_MAX_DATA, _INCOMPLETE_VARINT_2BYTE))
    )
    assert ret > 0, "カプセルの注入に失敗しました"
    assert server.get_session_ids() == [], "受理前にセッションが確立しています"

    # 受理すると蓄積したカプセルが排出され、プロトコル違反としてリセットされる
    assert server.accept_session(session_id) is True, "セッションの受理に失敗しました"
    wire = server.send()
    assert wire is not None, "RST_STREAM が送出されませんでした"
    assert _encode_rst_stream_frame(session_id, _PROTOCOL_ERROR) in wire, (
        "PROTOCOL_ERROR の RST_STREAM が送出されていません"
    )
    # 初期クレジットは送出されない (受理が成立していれば送られる値で確認する)
    config = h2.Config()
    assert _encode_capsule(_WT_MAX_DATA, _encode_varint(config.wt_initial_max_data)) not in wire, (
        "初期クレジットの WT_MAX_DATA が送出されました"
    )
    for capsule_type, value in (
        (_WT_MAX_STREAMS_BIDI, config.wt_initial_max_streams_bidi),
        (_WT_MAX_STREAMS_UNI, config.wt_initial_max_streams_uni),
    ):
        assert _encode_capsule(capsule_type, _encode_varint(value)) not in wire, (
            "初期クレジットの WT_MAX_STREAMS が送出されました"
        )

    assert server.get_session_ids() == [], "セッションが終了していません"
    closed_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1, "SessionClosed が 1 件だけ発火していません"
    assert closed_events[0].error_code == _PROTOCOL_ERROR
