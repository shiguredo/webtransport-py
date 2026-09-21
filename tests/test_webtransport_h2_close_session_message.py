"""WebTransport over HTTP/2 の WT_CLOSE_SESSION メッセージ検証テスト

draft-15 Section 6.12 の MUST 「Application Error Message が 1024 バイト超
または不正な UTF-8 なら WT_ERROR セッションエラー」を検証する。WT_ERROR の
値 (0x52) は draft-15 Section 3.4 の 0xTBD のプレースホルダであり、draft で
値が確定したら更新する。不正メッセージは close_session へ渡さず、固定の英語
メッセージを Error イベントと WT_CLOSE_SESSION の両方に使う。

同節の Application Error Code は 32 ビット固定であり、4 バイト未満のペイロード
は RFC 9297 Section 3.3 の「定義が挙げるフィールドの終端より前に終わる
ペイロード」に該当する。この固定長フィールドの欠落は可変長整数のデコード失敗
とは別経路のため、PROTOCOL_ERROR のストリームエラー (RFC 9113 Section 8.1.1)
になることも検証する。

Application Error Message が空 (ペイロード 4 バイト) でも正しいカプセルである。
draft-15 Section 3.4 がメッセージを "an optional explanatory message" と定め、
Section 6.12 の "The message takes up the remainder of the capsule" に下限が
無いためである。4 バイト (メッセージ無し) と 5 バイト以上 (メッセージ付き) は
従来どおり受理される (対照)。
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
)

from webtransport import h2
from webtransport.webtransport_ext.h2 import WtErrorCode

# エラーコードは WtErrorCode を単一の出典とする (draft-15 Section 3.4 の
# 0x52 は 0xTBD のプレースホルダ)
WT_ERROR = WtErrorCode.WT_ERROR.value


_MSG_TOO_LONG = "WT_CLOSE_SESSION message exceeds 1024 bytes"
_MSG_BAD_UTF8 = "WT_CLOSE_SESSION message is not valid UTF-8"

# Application Error Code に使う 0 以外の値 (0 では「ペイロードの値が
# SessionClosed に載る」の表明が空虚になる)。4 バイトすべてを異なる値にして、
# バイト順を取り違えた復号も検出できるようにする
_ERROR_CODE = 0x01020304


def _encode_wt_close_session_payload(error_code: int, message: bytes) -> bytes:
    """Application Error Code (4 バイト) + Application Error Message のペイロードを組む

    4 バイト未満のペイロード (code の欠落) はこのヘルパを通さず
    `_inject_close_session_payload` へ直接渡す。
    """
    return error_code.to_bytes(4, "big") + message


def _encode_wt_close_session_capsule(error_code: int, message: bytes) -> bytes:
    """WT_CLOSE_SESSION capsule のワイヤバイト列を組み立てる

    メッセージは bytes のまま載せる。WT_ERROR の WT_CLOSE_SESSION が送出された
    ことを検証する期待値の組み立てに使う。
    """
    return _encode_capsule(0x2843, _encode_wt_close_session_payload(error_code, message))


def _assert_wt_error_sent(server: h2.Session, error_message: str) -> None:
    """WT_ERROR (0x52) の WT_CLOSE_SESSION が送出されることを確認

    エラー検知は close_session (WT_CLOSE_SESSION 送出 + END_STREAM) で実現
    される (draft-15 Section 3.4)。ワイヤ部分列チェックで送出を検証する。
    """
    wire = server.send()
    assert wire is not None
    expected = _encode_wt_close_session_capsule(WT_ERROR, error_message.encode("utf-8"))
    assert expected in wire


def _assert_accepted_without_error_signals(server: h2.Session, session_id: int) -> None:
    """受理された WT_CLOSE_SESSION で PROTOCOL_ERROR の RST_STREAM も WT_CLOSE_SESSION も送出されないことを確認

    _WT_CLOSE_SESSION_TYPE_BYTES は WT_CLOSE_SESSION (Type 0x2843) の Type バイト
    列であり、エラー検知 (close_session 呼び出し) があれば必ずワイヤに現れる。
    応答カプセルを送らないのは draft-15 Section 6.12 が許容する省略 (MUST NOT
    ではない) であり、本実装のポリシーである。一方、同節は受信者に
    "replying with an HTTP/2 frame with the END_STREAM flag set" を MUST で
    課すため、その応答の存在は表明する。PROTOCOL_ERROR の RST_STREAM は 4 バイト
    未満の WT_CLOSE_SESSION を malformed として扱ったときに送出されるため、
    受理側ではその不在も表明する (受理と誤リセットが同時に起きる実装を検出
    する)。

    server.send() は 1 回だけ呼び、同一の wire に対して表明する (2 回呼ぶと
    1 回目の結果を見失い表明が空虚になる)。
    """
    wire = server.send()
    assert wire is not None, "受理後の応答が送出されていません"
    assert _encode_data_frame(session_id, b"", end_stream=True) in wire, (
        "受理後の応答 (END_STREAM) が送出されていません"
    )
    assert _encode_rst_stream_frame(session_id, _PROTOCOL_ERROR) not in wire, (
        "受理されるべきカプセルで RST_STREAM が送出されました"
    )
    assert _WT_CLOSE_SESSION_TYPE_BYTES not in wire, "WT_CLOSE_SESSION が送出されました"


def _inject_close_session_payload(server: h2.Session, session_id: int, payload: bytes) -> None:
    """生のペイロードを WT_CLOSE_SESSION カプセルとしてサーバーへ注入する

    4 バイト未満のペイロード (Application Error Code の欠落) と、任意の
    error code を再現するために使う。
    """
    ret = server.receive(_encode_data_frame(session_id, _encode_capsule(0x2843, payload)))
    assert ret > 0, "WT_CLOSE_SESSION カプセルの注入に失敗しました"


def _inject_close_session(server: h2.Session, session_id: int, message: bytes) -> None:
    """Application Error Code 0 + message の WT_CLOSE_SESSION を注入する"""
    _inject_close_session_payload(server, session_id, _encode_wt_close_session_payload(0, message))


def test_wt_close_session_message_over_1024_sends_wt_error() -> None:
    """1024 バイト超のメッセージで WT_ERROR になることを確認

    修正前は無検証で SessionClosed に渡し、セッションエラーにしなかった。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_close_session(server, session_id, b"a" * 1025)
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == WT_ERROR
    assert error_events[0].error_message == _MSG_TOO_LONG
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.SESSION_CLOSED for event in events)
    _assert_wt_error_sent(server, _MSG_TOO_LONG)


def test_wt_close_session_invalid_utf8_sends_wt_error() -> None:
    """不正な UTF-8 のメッセージで WT_ERROR になることを確認

    受信した不正バイト列は Error / WT_CLOSE_SESSION に載せない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_close_session(server, session_id, b"\xff")
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == WT_ERROR
    assert error_events[0].error_message == _MSG_BAD_UTF8
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.SESSION_CLOSED for event in events)
    _assert_wt_error_sent(server, _MSG_BAD_UTF8)


def test_wt_close_session_overlong_utf8_sends_wt_error() -> None:
    """overlong 符号化の UTF-8 で WT_ERROR になることを確認

    U+0000 の overlong 2 バイト (0xC0 0x80) は RFC 3629 では不正。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_close_session(server, session_id, b"\xc0\x80")
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == WT_ERROR
    assert error_events[0].error_message == _MSG_BAD_UTF8
    assert error_events[0].session_id == session_id
    assert all(event.type != h2.EventType.SESSION_CLOSED for event in events)
    _assert_wt_error_sent(server, _MSG_BAD_UTF8)


def test_wt_close_session_message_exactly_1024_is_accepted() -> None:
    """1024 バイトちょうど・正しい UTF-8 はセッションエラーにならないことを確認 (対照)"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    message = b"a" * 1024
    _inject_close_session(server, session_id, message)
    events = _drain_events(server)
    closed_events = [event for event in events if event.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].error_code == 0
    assert closed_events[0].error_message == "a" * 1024
    assert closed_events[0].session_id == session_id
    assert all(event.type != h2.EventType.ERROR for event in events)
    _assert_accepted_without_error_signals(server, session_id)


def test_wt_close_session_valid_utf8_is_accepted() -> None:
    """短い正しい UTF-8 メッセージは SessionClosed にそのまま届くことを確認 (対照)"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_close_session(server, session_id, "終了".encode())
    events = _drain_events(server)
    closed_events = [event for event in events if event.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].error_code == 0
    assert closed_events[0].error_message == "終了"
    assert closed_events[0].session_id == session_id
    assert all(event.type != h2.EventType.ERROR for event in events)
    _assert_accepted_without_error_signals(server, session_id)


@pytest.mark.parametrize(
    "payload",
    [b"", b"\x00", b"\x00\x00", b"\x00\x00\x00"],
    ids=["0byte", "1byte", "2byte", "3byte"],
)
def test_wt_close_session_short_error_code_resets_stream(payload: bytes) -> None:
    """Application Error Code が 4 バイト未満の WT_CLOSE_SESSION で RST_STREAM が出ることを確認

    4 バイト未満のペイロードは RFC 9297 Section 3.3 の「定義が挙げるフィールドの
    終端より前に終わるペイロード」に該当し、PROTOCOL_ERROR のストリームエラー
    (RFC 9113 Section 8.1.1) になる (根拠はモジュール docstring)。修正前は
    error code 0 の正常終了として受理されていたため、ここでの RST_STREAM の
    表明が失敗する。表明の詳細 (WT_CLOSE_SESSION と Error イベントの不在、
    SessionClosed が 1 回で error_code が PROTOCOL_ERROR) は
    `_assert_session_closed_by_protocol_error` が担う。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_close_session_payload(server, session_id, payload)

    _assert_session_closed_by_protocol_error(server, client, session_id)


def test_wt_close_session_four_byte_error_code_is_accepted() -> None:
    """Application Error Code が 4 バイトちょうどの WT_CLOSE_SESSION が受理されることを確認

    Application Error Message が空でも正しいカプセルである (対照。根拠は
    モジュール docstring)。error code に 0 以外を使い、ペイロードの値がそのまま
    SessionClosed に載ることを確かめる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_close_session_payload(
        server, session_id, _encode_wt_close_session_payload(_ERROR_CODE, b"")
    )
    events = _drain_events(server)
    closed_events = [event for event in events if event.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1, "SessionClosed が 1 件だけ発火していません"
    assert closed_events[0].error_code == _ERROR_CODE, "ペイロードの error code が載っていません"
    assert closed_events[0].error_message == ""
    assert closed_events[0].session_id == session_id
    assert all(event.type != h2.EventType.ERROR for event in events), "Error イベントが発火しました"
    _assert_accepted_without_error_signals(server, session_id)


def test_wt_close_session_error_code_and_message_are_accepted() -> None:
    """5 バイト以上で error code とメッセージの両方が SessionClosed に載ることを確認

    4 バイトの Application Error Code の後ろが Application Error Message に
    なる (対照)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    message = "終了".encode()
    _inject_close_session_payload(
        server, session_id, _encode_wt_close_session_payload(_ERROR_CODE, message)
    )
    events = _drain_events(server)
    closed_events = [event for event in events if event.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1, "SessionClosed が 1 件だけ発火していません"
    assert closed_events[0].error_code == _ERROR_CODE
    assert closed_events[0].error_message == "終了"
    assert closed_events[0].session_id == session_id
    assert all(event.type != h2.EventType.ERROR for event in events), "Error イベントが発火しました"
    _assert_accepted_without_error_signals(server, session_id)
