"""WebTransport over HTTP/2 の余分なバイトを持つカプセルの検証テスト

RFC 9297 Section 3.3 は「各カプセルのペイロードはその定義が挙げるフィールドを
正確に含まなければならない。識別フィールドの後に余分なバイトを含むペイロード
は malformed な HTTP メッセージとして扱わなければならない」ことを MUST として
おり、HTTP/2 では RFC 9113 Section 8.1.1 により PROTOCOL_ERROR のストリーム
エラー (RST_STREAM) を送出する。draft-ietf-webtrans-http2-15 Section 3.4 の
とおり、ストリームのリセットはセッションを終了させる。

対象は固定フィールドのみからなる 8 ハンドラ (WT_RESET_STREAM / WT_STOP_SENDING /
WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS / WT_STREAM_DATA_BLOCKED /
WT_STREAMS_BLOCKED / WT_DATA_BLOCKED) と、ペイロードを持てない WT_DRAIN_SESSION
である。残りのバイトが正当なペイロードであるカプセル (WT_STREAM / DATAGRAM /
WT_CLOSE_SESSION / PADDING) には適用しない。

検証の順序も固定する。WT_RESET_STREAM の Error Code の範囲検証は既存の順序
(Reliable Size より前) を維持するため、余分なバイトと範囲外の Error Code を
同時に持つ入力では範囲検証が先に走り WT_ERROR セッションエラーになる。他の
カプセルは形の検証が意味論検証 (減少値・方向・ストリーム状態) より先に走る。
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
from webtransport.webtransport_ext.h2 import WtErrorCode

# セッションエラー (draft-ietf-webtrans-http2-15 Section 3.4)
_WT_ERROR = WtErrorCode.WT_ERROR.value

# カプセル種別 (draft-ietf-webtrans-http2-15 Section 6。WT_DRAIN_SESSION の
# 0x78ae は draft-ietf-webtrans-http3-16 Section 4.6 の登録値である)
_WT_RESET_STREAM = 0x190B4D39
_WT_STOP_SENDING = 0x190B4D3A
_WT_MAX_DATA = 0x190B4D3D
_WT_MAX_STREAM_DATA = 0x190B4D3E
_WT_MAX_STREAMS_BIDI = 0x190B4D3F
_WT_MAX_STREAMS_UNI = 0x190B4D40
_WT_DATA_BLOCKED = 0x190B4D41
_WT_STREAM_DATA_BLOCKED = 0x190B4D42
_WT_STREAMS_BLOCKED_BIDI = 0x190B4D43
_WT_STREAMS_BLOCKED_UNI = 0x190B4D44
_WT_DRAIN_SESSION = 0x78AE
_WT_PADDING = 0x190B4D38
_WT_DATAGRAM = 0x00

# 余分なバイト (1 バイトと複数バイト)
_TRAILING_BYTE = b"\x00"
_TRAILING_BYTES = b"\x00\x00"

# Application Protocol Error Code の上限 (draft-15 Section 6.2 / 6.3)。
# 超過は WT_ERROR セッションエラーになる
_OVER_MAX_ERROR_CODE = 0x100000000

# 本実装の Config の既定値。セッション確立時にピアの SETTINGS から引き継いだ
# 受信済み値を下回る WT_MAX_DATA / WT_MAX_STREAM_DATA は既存の減少値検証
# (WT_FLOW_CONTROL_ERROR) で閉じるため、対照テストには既定値より大きい値を使う。
# 余分なバイトを持つ側は形の検証が意味論検証より先に走るため値に依存しない
_ACCEPTED_MAX_DATA = h2.Config().wt_initial_max_data + 1
_ACCEPTED_MAX_STREAM_DATA = h2.Config().wt_initial_max_stream_data + 1


@pytest.mark.parametrize(
    ("capsule_type", "payload", "unexpected_event"),
    [
        # WT_RESET_STREAM: Stream ID / Application Error Code / Reliable Size
        (
            _WT_RESET_STREAM,
            _encode_varint(0) + _encode_varint(0) + _encode_varint(0) + _TRAILING_BYTE,
            h2.EventType.STREAM_RESET,
        ),
        # WT_STOP_SENDING: Stream ID / Application Error Code
        (
            _WT_STOP_SENDING,
            _encode_varint(0) + _encode_varint(0) + _TRAILING_BYTE,
            h2.EventType.STOP_SENDING,
        ),
        # WT_MAX_DATA: Maximum Data (余分なバイトが複数の場合も同じ経路)
        (_WT_MAX_DATA, _encode_varint(_ACCEPTED_MAX_DATA) + _TRAILING_BYTE, None),
        (_WT_MAX_DATA, _encode_varint(_ACCEPTED_MAX_DATA) + _TRAILING_BYTES, None),
        # WT_MAX_STREAM_DATA: Stream ID / Maximum Stream Data。既定値未満の値
        # (減少値検証に掛かる値) を使い、形の検証が先に走ることも固定する
        (
            _WT_MAX_STREAM_DATA,
            _encode_varint(0) + _encode_varint(100) + _TRAILING_BYTE,
            None,
        ),
        # WT_MAX_STREAMS: Maximum Streams (両方向 / 単方向)
        (_WT_MAX_STREAMS_BIDI, _encode_varint(100) + _TRAILING_BYTE, None),
        (_WT_MAX_STREAMS_UNI, _encode_varint(100) + _TRAILING_BYTE, None),
        # WT_DATA_BLOCKED: Maximum Data。形の検証が意味論検証より先に走ることを
        # 固定する (WT_MAX_DATA の減少値検証へ誤って流れた場合の検出は、
        # 既定値未満の値を受理させる対照表の行が担う)
        (_WT_DATA_BLOCKED, _encode_varint(100) + _TRAILING_BYTE, None),
        # WT_STREAM_DATA_BLOCKED: Stream ID / Maximum Stream Data
        (
            _WT_STREAM_DATA_BLOCKED,
            _encode_varint(0) + _encode_varint(100) + _TRAILING_BYTE,
            None,
        ),
        # WT_STREAMS_BLOCKED: Maximum Streams (両方向 / 単方向)
        (_WT_STREAMS_BLOCKED_BIDI, _encode_varint(100) + _TRAILING_BYTE, None),
        (_WT_STREAMS_BLOCKED_UNI, _encode_varint(100) + _TRAILING_BYTE, None),
        # WT_DRAIN_SESSION: Length は 0 (payload があれば余分なバイト)
        (_WT_DRAIN_SESSION, _TRAILING_BYTE, h2.EventType.SESSION_DRAINING),
    ],
    ids=[
        "reset_stream",
        "stop_sending",
        "max_data",
        "max_data_two_trailing_bytes",
        "max_stream_data",
        "max_streams_bidi",
        "max_streams_uni",
        "data_blocked",
        "stream_data_blocked",
        "streams_blocked_bidi",
        "streams_blocked_uni",
        "drain_session",
    ],
)
def test_trailing_byte_capsule_resets_stream(
    capsule_type: int,
    payload: bytes,
    unexpected_event: h2.EventType | None,
) -> None:
    """余分なバイトを持つカプセルで PROTOCOL_ERROR の RST_STREAM が送出される"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(_encode_data_frame(session_id, _encode_capsule(capsule_type, payload)))
    assert ret > 0, "カプセルの注入に失敗しました"

    _assert_session_closed_by_protocol_error(server, client, session_id, unexpected_event)


@pytest.mark.parametrize(
    ("capsule_type", "payload", "expected_event"),
    [
        # WT_RESET_STREAM: Reliable Size は受信済みバイト数 (0) と一致させる
        (
            _WT_RESET_STREAM,
            _encode_varint(0) + _encode_varint(0) + _encode_varint(0),
            h2.EventType.STREAM_RESET,
        ),
        (
            _WT_STOP_SENDING,
            _encode_varint(0) + _encode_varint(0),
            h2.EventType.STOP_SENDING,
        ),
        (_WT_MAX_DATA, _encode_varint(_ACCEPTED_MAX_DATA), None),
        (
            _WT_MAX_STREAM_DATA,
            _encode_varint(0) + _encode_varint(_ACCEPTED_MAX_STREAM_DATA),
            None,
        ),
        (_WT_MAX_STREAMS_BIDI, _encode_varint(100), None),
        (_WT_MAX_STREAMS_UNI, _encode_varint(100), None),
        # 既定値未満の 100。値の意味論 (減少値検証) へ誤って流れていないことを
        # セッション存続の表明で検出できる
        (_WT_DATA_BLOCKED, _encode_varint(100), None),
        (_WT_STREAM_DATA_BLOCKED, _encode_varint(0) + _encode_varint(100), None),
        (_WT_STREAMS_BLOCKED_BIDI, _encode_varint(100), None),
        (_WT_STREAMS_BLOCKED_UNI, _encode_varint(100), None),
        # WT_DRAIN_SESSION: Length 0
        (_WT_DRAIN_SESSION, b"", h2.EventType.SESSION_DRAINING),
        # 残りのバイトが正当なペイロードであるカプセルには検証を掛けない
        # (過剰適用していないことの固定)
        (_WT_PADDING, _TRAILING_BYTES, None),
        (_WT_DATAGRAM, b"datagram", h2.EventType.DATAGRAM),
    ],
    ids=[
        "reset_stream",
        "stop_sending",
        "max_data",
        "max_stream_data",
        "max_streams_bidi",
        "max_streams_uni",
        "data_blocked",
        "stream_data_blocked",
        "streams_blocked_bidi",
        "streams_blocked_uni",
        "drain_session",
        "padding",
        "datagram",
    ],
)
def test_capsule_without_trailing_byte_is_accepted(
    capsule_type: int,
    payload: bytes,
    expected_event: h2.EventType | None,
) -> None:
    """余分なバイトを持たないカプセルは従来どおり受理される (対照)"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(_encode_data_frame(session_id, _encode_capsule(capsule_type, payload)))
    assert ret > 0, "カプセルの注入に失敗しました"

    wire = server.send()
    assert wire is None or _encode_rst_stream_frame(session_id, _PROTOCOL_ERROR) not in wire, (
        "受理されるべきカプセルで RST_STREAM が送出されました"
    )

    events = _drain_events(server)
    assert all(event.type != h2.EventType.ERROR for event in events), "Error イベントが発火しました"
    if expected_event is not None:
        assert any(event.type == expected_event for event in events), (
            f"{expected_event} イベントが発火していません"
        )
    assert server.get_session_ids() == [session_id], "セッションが終了していません"


def test_reset_stream_over_max_error_code_takes_precedence_over_trailing_byte() -> None:
    """余分なバイトと範囲外の Error Code を持つ WT_RESET_STREAM では範囲検証が先に走る

    WT_RESET_STREAM の Error Code の範囲検証は既存の順序 (Reliable Size より前)
    を維持するため、形の検証より先に成立する。この入力では WT_ERROR
    セッションエラーになり、PROTOCOL_ERROR の RST_STREAM は送出しない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    payload = (
        _encode_varint(0)
        + _encode_varint(_OVER_MAX_ERROR_CODE)
        + _encode_varint(0)
        + _TRAILING_BYTE
    )
    ret = server.receive(_encode_data_frame(session_id, _encode_capsule(_WT_RESET_STREAM, payload)))
    assert ret > 0, "カプセルの注入に失敗しました"

    wire = server.send()
    assert wire is not None, "WT_CLOSE_SESSION が送出されませんでした"
    assert _encode_rst_stream_frame(session_id, _PROTOCOL_ERROR) not in wire, (
        "範囲検証より先に形の検証が走っています"
    )
    assert _WT_CLOSE_SESSION_TYPE_BYTES in wire, "WT_CLOSE_SESSION が送出されていません"

    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1, "WT_ERROR の Error イベントが 1 件だけ発火していません"
    assert error_events[0].error_code == _WT_ERROR
    assert server.get_session_ids() == [], "セッションが終了していません"


def test_stop_sending_trailing_byte_check_precedes_error_code_range() -> None:
    """余分なバイトと範囲外の Error Code を持つ WT_STOP_SENDING では形の検証が先に走る

    WT_STOP_SENDING の Error Code の範囲検証は形の検証より後ろにあるため、
    余分なバイトを持つ入力では PROTOCOL_ERROR の RST_STREAM になる
    (WT_ERROR セッションエラーにはならない)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    payload = _encode_varint(0) + _encode_varint(_OVER_MAX_ERROR_CODE) + _TRAILING_BYTE
    ret = server.receive(_encode_data_frame(session_id, _encode_capsule(_WT_STOP_SENDING, payload)))
    assert ret > 0, "カプセルの注入に失敗しました"

    _assert_session_closed_by_protocol_error(server, client, session_id, h2.EventType.STOP_SENDING)


def test_trailing_byte_in_pre_accept_buffer_resets_on_accept() -> None:
    """受理前に蓄積した余分なバイト付きカプセルが accept_session の排出でリセットされる

    サーバーは受理前のカプセルを蓄積し、accept_session で遅延処理する
    (draft-15 Section 3.2 の楽観的送信)。この経路でも余分なバイトを持つ
    カプセルは PROTOCOL_ERROR の RST_STREAM になり、初期クレジット
    (WT_MAX_DATA / WT_MAX_STREAMS) は送出されない。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # 受理前に余分なバイトを持つカプセルを届ける (この時点では処理されない)
    ret = server.receive(
        _encode_data_frame(
            session_id,
            _encode_capsule(_WT_MAX_DATA, _encode_varint(_ACCEPTED_MAX_DATA) + _TRAILING_BYTE),
        )
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
