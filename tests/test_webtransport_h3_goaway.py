"""WebTransport over HTTP/3 の GOAWAY 受信テスト

Sans-IO 構成 (conftest.py の Sans-IO ヘルパー) を使い、GOAWAY 受信が接続の
終了ではなく graceful shutdown の通知として扱われることを検証する。
draft-ietf-webtrans-http3-16 Section 4.7 は「WT_DRAIN_SESSION カプセルまたは
HTTP/3 GOAWAY フレームを送受信した後も、エンドポイントはセッションを継続して
よい」と定め、RFC 9114 Section 5.2 は graceful shutdown の完了時に
H3_NO_ERROR を使う SHOULD を定める。GOAWAY 受信で is_closed() が真になると、
高レベル層が H3_GENERAL_PROTOCOL_ERROR で接続を閉じてしまい、正当な
graceful shutdown がプロトコル違反として扱われる。

GOAWAY を送出する公開 API は h3 バインディングに無いため、ピアの制御
ストリームへ GOAWAY フレームを注入して再現する。注入する GOAWAY ID は
client 側セッションでは CONNECT ストリーム ID より大きい値にする: RFC 9114
Section 5.2 は GOAWAY フレームの識別子以上の識別子を持つ要求を拒否するため、
ID 0 では「既存セッションを継続できる」ことを表明できない。あわせて、
GOAWAY 受信後もデータグラムを送受信できること、GOAWAY ID がイベントで
通知されること、低レベル層は GOAWAY ごとにイベントを積み (初回のみ通知する
抑止は高レベル層の責務)、GOAWAY ID が増加しない限り接続が閉じられないことを
検証する。
"""

from __future__ import annotations

from conftest import (
    _drain_events,
    _encode_h3_goaway_frame,
    _encode_wt_datagram,
    _establish_session,
    _pump,
)

from webtransport import h3

# クライアントの制御ストリーム ID (クライアントの単方向ストリームは %4 == 2)
_CLIENT_CONTROL_STREAM_ID = 2

# サーバーの制御ストリーム ID (サーバーの単方向ストリームは %4 == 3)
_SERVER_CONTROL_STREAM_ID = 3

# サーバーが送出する GOAWAY の ID。CONNECT ストリーム ID (0) より大きい値を使う
_GOAWAY_ID = 4

# 2 回目の GOAWAY の ID。RFC 9114 Section 5.2 は識別子の増加を接続エラーとし、
# nghttp3 も増加を NGHTTP3_ERR_H3_ID_ERROR にする。client 接続では GOAWAY の ID は
# クライアント起動双方向ストリーム ID (4 の倍数) でなければならないため、
# 4 より小さい 0 を使う (同一 ID の再受信は nghttp3 が疑わしいものとして扱う)
_SECOND_GOAWAY_ID = 0


def _goaway_events(session: h3.Session) -> list[int]:
    """セッションに積まれた GoAway イベントの GOAWAY ID を到着順に返す"""
    return [
        event.goaway_id for event in _drain_events(session) if event.type == h3.EventType.GOAWAY
    ]


def test_goaway_does_not_close_session() -> None:
    """GOAWAY 受信で is_closed() が真にならず GoAway イベントが 1 回発火することを確認

    GOAWAY は graceful shutdown の通知であり接続エラーではない
    (draft-ietf-webtrans-http3-16 Section 4.7 / RFC 9114 Section 5.2)。
    shutdown コールバックで closed_ を立てると、高レベル層が
    H3_GENERAL_PROTOCOL_ERROR で接続を閉じてしまう。
    """
    client, _server, _session_id = _establish_session()

    ret = client.receive_stream_data(
        _SERVER_CONTROL_STREAM_ID, _encode_h3_goaway_frame(_GOAWAY_ID), False
    )
    assert ret > 0, "GOAWAY フレームの注入に失敗しました"

    assert client.is_closed() is False, "GOAWAY 受信で接続が閉じられた扱いになっています"
    assert _goaway_events(client) == [_GOAWAY_ID], (
        "GoAway イベントが GOAWAY ID 付きで 1 件だけ発火していません"
    )


def test_server_session_goaway_does_not_close_session() -> None:
    """サーバー側セッションでも GOAWAY 受信で is_closed() が偽のままであることを確認

    サーバーが受信する GOAWAY の ID は push ID を表す (RFC 9114 Section 7.2.6)。
    どちらの方向でも graceful shutdown の通知であり、接続を閉じない
    (draft-ietf-webtrans-http3-16 Section 4.7)。
    """
    _client, server, _session_id = _establish_session()

    ret = server.receive_stream_data(
        _CLIENT_CONTROL_STREAM_ID, _encode_h3_goaway_frame(_GOAWAY_ID), False
    )
    assert ret > 0, "GOAWAY フレームの注入に失敗しました"

    assert server.is_closed() is False, "GOAWAY 受信で接続が閉じられた扱いになっています"
    assert _goaway_events(server) == [_GOAWAY_ID], (
        "GoAway イベントが GOAWAY ID 付きで 1 件だけ発火していません"
    )


def test_goaway_keeps_datagram_sendable() -> None:
    """GOAWAY 受信後もデータグラムを送信でき、ピアが受信できることを確認

    GOAWAY 受信後もエンドポイントはセッションを継続してよい
    (draft-ietf-webtrans-http3-16 Section 4.7)。GOAWAY 受信で送信経路
    (get_streams_to_send / send_datagram) が止まると、graceful shutdown 中の
    データグラムが失われる。
    """
    client, server, session_id = _establish_session()

    ret = client.receive_stream_data(
        _SERVER_CONTROL_STREAM_ID, _encode_h3_goaway_frame(_GOAWAY_ID), False
    )
    assert ret > 0, "GOAWAY フレームの注入に失敗しました"
    assert _goaway_events(client) == [_GOAWAY_ID], "GoAway イベントが発火していません"

    # GOAWAY 受信後もデータグラムを送出できる
    client.send_datagram(session_id, b"after-goaway")
    datagrams = client.get_datagrams_to_send()
    assert datagrams == [_encode_wt_datagram(session_id, b"after-goaway")], (
        "GOAWAY 受信後にデータグラムが送出できません"
    )

    # ピア (サーバー) に届いて Datagram イベントになる (セッションは生存している)
    for datagram in datagrams:
        server.receive_datagram(datagram)
    datagram_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.DATAGRAM
    ]
    assert [event.data for event in datagram_events] == [b"after-goaway"], (
        "GOAWAY 受信後にピアがデータグラムを受信できません"
    )


def test_goaway_keeps_existing_stream_usable() -> None:
    """GOAWAY 受信後も既存の WT データストリームで送受信できることを確認

    GOAWAY 受信後もエンドポイントはセッションを継続してよい
    (draft-ietf-webtrans-http3-16 Section 4.7)。nghttp3 が GOAWAY 受信で拒否するのは
    新規ストリームの開設 (nghttp3_conn_open_wt_data_stream) だけで、既存ストリームの
    送受信は継続できる。
    """
    client, server, session_id = _establish_session()

    # 確立済みセッションでデータストリームを開き、GOAWAY 受信前にデータを送る
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"before-goaway", fin=False)
    _pump(client, server)
    before_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.STREAM_DATA
    ]
    assert [event.data for event in before_events] == [b"before-goaway"], (
        "GOAWAY 受信前にデータストリームで送信できません"
    )

    # GOAWAY を受信する
    ret = client.receive_stream_data(
        _SERVER_CONTROL_STREAM_ID, _encode_h3_goaway_frame(_GOAWAY_ID), False
    )
    assert ret > 0, "GOAWAY フレームの注入に失敗しました"
    assert _goaway_events(client) == [_GOAWAY_ID], "GoAway イベントが発火していません"

    # GOAWAY 受信後も既存ストリームで送信でき、ピアが受信できる
    client.send_stream_data(stream_id, b"after-goaway", fin=True)
    _pump(client, server)
    after_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.STREAM_DATA
    ]
    assert [event.data for event in after_events] == [b"after-goaway"], (
        "GOAWAY 受信後に既存ストリームで送信できません"
    )


def test_multiple_goaway_notifies_each_and_keeps_session() -> None:
    """GOAWAY を複数回受信してもイベントは毎回積まれ、接続は閉じられないことを確認

    低レベル層は受信した GOAWAY ごとに GoAway イベントを積む (初回のみ通知する
    抑止は高レベル層の責務)。RFC 9114 Section 5.2 は GOAWAY の識別子の増加を
    接続エラーとするため、2 回目は小さい ID を注入する。
    """
    client, _server, _session_id = _establish_session()

    ret = client.receive_stream_data(
        _SERVER_CONTROL_STREAM_ID, _encode_h3_goaway_frame(_GOAWAY_ID), False
    )
    assert ret > 0, "1 回目の GOAWAY フレームの注入に失敗しました"
    ret = client.receive_stream_data(
        _SERVER_CONTROL_STREAM_ID, _encode_h3_goaway_frame(_SECOND_GOAWAY_ID), False
    )
    assert ret > 0, "2 回目の GOAWAY フレームの注入に失敗しました"

    assert client.is_closed() is False, "GOAWAY の再受信で接続が閉じられた扱いになっています"
    assert _goaway_events(client) == [_GOAWAY_ID, _SECOND_GOAWAY_ID], (
        "GOAWAY の受信ごとに GoAway イベントが積まれていません"
    )
