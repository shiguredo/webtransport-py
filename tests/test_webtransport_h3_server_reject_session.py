"""WebTransport over HTTP/3 サーバーの非 2xx 拒否時のセッション ID 削除テスト

サーバーが reject_session (非 2xx 応答) で CONNECT リクエストを拒否しても、
サーバーの session_ids_ にセッション ID が残り続け、拒否されたセッション
ID 宛の send_datagram がデータグラムを送出し、receive_datagram が配信し
続ける問題の修正を検証する。draft-ietf-webtrans-http3-16 Section 3.2 の
「サーバーの視点では、2xx 応答を送信した時点でセッションが確立される」
により、非 2xx で拒否されたセッションは一度も確立されておらず、終了通知
(SessionClosed) の意味論が合わないため黙って削除する。
"""

from __future__ import annotations

import pytest
from conftest import (
    _connect_session,
    _create_session_pair,
    _drain_events,
    _encode_wt_datagram,
    _pump,
    _setup_connect,
)

from webtransport import h3


def _deliver_connect_request(client: h3.Session, server: h3.Session) -> None:
    """CONNECT リクエストをサーバーに届けて session_ids_ に挿入する

    end_headers_cb により session_ids_ にセッション ID が挿入され、
    SESSION_READY イベントが積まれる。accept_session は呼ばない (拒否経路
    の検証のため)。
    """
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)


@pytest.mark.parametrize(
    "status_code",
    [103, 403, 302, 500],
    ids=["informational", "forbidden", "redirect", "server_error"],
)
def test_server_reject_non_2xx_removes_session_id(status_code: int) -> None:
    """非 2xx 拒否でサーバーの session_ids_ からセッション ID が削除されることを確認

    1xx 中間応答も確立応答ではない (Section 3.2 では 2xx のみが確立)。
    HTTP 上 1xx は最終応答として送出されないが、本テストは非 2xx 判定の
    分岐 (status_code / 100 != 2) の検証のため reject_session に 1xx を
    渡して分岐を確認する。削除後は get_session_ids() に現れない。
    """
    client, server = _create_session_pair()
    _deliver_connect_request(client, server)
    assert server.get_session_ids() == [0]

    # サーバーが非 2xx で拒否する
    server.reject_session(0, status_code)
    server.get_streams_to_send()

    # session_ids_ から削除されている
    assert server.get_session_ids() == []


def test_server_reject_2xx_keeps_session_id() -> None:
    """2xx 非 200 応答 (201) ではセッション ID が削除されず send_datagram が送出されることを確認

    reject_session は任意の status_code を受け付けており、2xx 非 200 応答は
    reject_session で生成する (accept_session は 200 固定)。サーバー視点では
    2xx 送出 = 確立 (draft-ietf-webtrans-http3-16 Section 3.2) のため、何も
    削除しない。なお confirm_wt_session は accept_session のみが呼ぶため、
    2xx を渡してもサーバー側の wt.session は生成されず、ストリーム送受信は
    通らない (テスト合成経路としての制約)。
    """
    client, server = _create_session_pair()
    _deliver_connect_request(client, server)

    # サーバーが 201 で応答する
    server.reject_session(0, 201)
    server.get_streams_to_send()

    # セッション ID は残り、send_datagram も送出される
    assert server.get_session_ids() == [0]
    server.send_datagram(0, b"alive")
    assert len(server.get_datagrams_to_send()) == 1


def test_server_reject_send_datagram_ignored() -> None:
    """拒否されたセッション ID 宛の send_datagram がデータグラムを送出しないことを確認

    拒否後は session_ids_ のメンバーシップ確認が成立しないため、
    send_datagram は黙って無視される (一度も確立されていないセッションへの
    送信は Section 3.2 の意味論で成立しない)。
    """
    client, server = _create_session_pair()
    _deliver_connect_request(client, server)

    # サーバーが 403 で拒否する
    server.reject_session(0, 403)
    server.get_streams_to_send()

    # 拒否後の send_datagram は送出されない
    server.send_datagram(0, b"after-reject")
    assert server.get_datagrams_to_send() == []


def test_server_reject_queued_datagram_still_sent() -> None:
    """拒否前にキューされたデータグラムは拒否後も送出されることを確認

    拒否を学習する前に send_datagram で pending_datagrams_ に積まれた
    データグラムは、削除後に get_datagrams_to_send でそのまま送出される
    (禁止対象は拒否後の新しい送信であり、既にキュー済みの送出は対象外)。
    """
    client, server = _create_session_pair()
    _deliver_connect_request(client, server)

    # 拒否前に send_datagram でキューに積む
    server.send_datagram(0, b"queued-before-reject")

    # サーバーが 403 で拒否する
    server.reject_session(0, 403)
    server.get_streams_to_send()

    # キュー済みのデータグラムは送出される
    assert server.get_datagrams_to_send() == [_encode_wt_datagram(0, b"queued-before-reject")]


def test_server_reject_receive_datagram_not_delivered() -> None:
    """拒否されたセッション ID 宛の receive_datagram がデータグラムを配信しないことを確認

    拒否後は session_ids_ のメンバーシップ確認が成立しないため、届いた
    データグラムは破棄されて Datagram イベントは発火しない (データグラムは
    再送されず配信保証がないため喪失は無害。draft-ietf-webtrans-http3-16
    Section 4.1 / RFC 9221)。
    """
    client, server = _create_session_pair()
    _deliver_connect_request(client, server)

    # サーバーが 403 で拒否する
    server.reject_session(0, 403)
    server.get_streams_to_send()

    # 拒否後のデータグラムは破棄され、Datagram イベントは発火しない
    server.receive_datagram(_encode_wt_datagram(0, b"after-reject"))
    assert all(e.type != h3.EventType.DATAGRAM for e in _drain_events(server))


def test_server_reject_no_session_closed_event() -> None:
    """拒否されたセッションに対して SessionClosed イベントが発火しないことを確認

    非 2xx で拒否されたセッションは一度も確立されていない
    (draft-ietf-webtrans-http3-16 Section 3.2) ため、SessionClosed (セッション
    終了の通知) の意味論が合わない。黙って削除する。
    """
    client, server = _create_session_pair()
    _deliver_connect_request(client, server)

    # サーバーが 403 で拒否する
    server.reject_session(0, 403)
    server.get_streams_to_send()

    # SessionClosed は発火しない
    assert all(e.type != h3.EventType.SESSION_CLOSED for e in _drain_events(server))


def test_server_reject_wt_close_session_after_reject_no_event() -> None:
    """拒否後に WT_CLOSE_SESSION が届いても SessionClosed / Error が発火しないことを確認

    拒否後にピアが送ってくる WT_CLOSE_SESSION カプセルは、nghttp3 が応答
    済み (拒否済み) の CONNECT ストリームでは recv_wt_close_session_cb を
    発火しないため、SessionClosed は発火せずセッション ID も残らない。
    """
    client, server = _create_session_pair()
    _deliver_connect_request(client, server)

    # サーバーが 403 で拒否する
    server.reject_session(0, 403)
    server.get_streams_to_send()

    # 拒否後にクライアントが WT_CLOSE_SESSION を送る
    client.close_session(0, 0)
    _pump(client, server)

    # SessionClosed / Error は発火せず、セッション ID も残らない
    assert server.get_session_ids() == []
    events = _drain_events(server)
    assert all(e.type != h3.EventType.SESSION_CLOSED for e in events)
    assert all(e.type != h3.EventType.ERROR for e in events)


def test_server_reject_close_stream_returns_minus_one() -> None:
    """拒否後の close_stream が 1 回目から -1 を返すことを確認

    拒否後は session_ids_ から削除されているため、close_stream の CONNECT
    ストリーム判定 (session_ids_ のメンバーシップ確認) が成立せず、1 回目
    から -1 を返す (二重発火の経路も残らない)。
    """
    client, server = _create_session_pair()
    _deliver_connect_request(client, server)

    # サーバーが 403 で拒否する
    server.reject_session(0, 403)
    server.get_streams_to_send()

    # close_stream は 1 回目から -1 を返す
    assert server.close_stream(0, 0) == -1


def test_server_reject_other_session_unaffected() -> None:
    """一方のセッションを拒否しても他方のセッションが影響を受けないことを確認

    session_ids_ からの削除はセッション ID 単位で行われる。確立済みの
    セッション 0 は拒否されたセッション 4 の影響を受けず、send_datagram の
    送出とデータグラムの配信が継続される。
    """
    client, server = _create_session_pair()

    # セッション 0 を通常確立する (accept まで完了)
    session_id = _connect_session(client, server, 0)
    assert server.get_session_ids() == [0]

    # セッション 4 を拒否する
    assert client.connect(4, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 4)
    server.receive_stream_data(4, headers, False)
    server.reject_session(4, 403)
    server.get_streams_to_send()
    assert server.get_session_ids() == [0]

    # セッション 0 の送受信は継続する
    server.send_datagram(session_id, b"alive")
    assert len(server.get_datagrams_to_send()) == 1
    server.receive_datagram(_encode_wt_datagram(session_id, b"incoming"))
    events = _drain_events(server)
    assert any(e.type == h3.EventType.DATAGRAM and e.session_id == session_id for e in events)
