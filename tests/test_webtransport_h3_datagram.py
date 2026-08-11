"""WebTransport over HTTP/3 のデータグラム送信テスト

Sans-IO 構成 (conftest.py の Sans-IO ヘルパー) を使い、セッション終了後の
send_datagram が無視されることを検証する。セッション終了の 3 経路
(close_stream / close_session / recv_wt_close_session_cb) すべてで検証する
(draft-ietf-webtrans-http3-16 Section 6 の MUST 「セッション終了を学習した
エンドポイントは、新しいデータグラムを送信してはならない」)。あわせて、
生存セッションへの送信と楽観的送信が妨げられないことの回帰防止も行う。
"""

from __future__ import annotations

import pytest
from conftest import (
    _connect_session,
    _create_session_pair,
    _encode_wt_datagram,
    _establish_session,
    _establish_two_sessions,
    _pump,
)

from webtransport import h3


def test_send_datagram_after_close_stream_ignored() -> None:
    """CONNECT ストリームのクローズ後に send_datagram が無視されることを確認

    close_stream による CONNECT ストリームのクローズはセッション終了の
    3 経路の 1 つ (draft-ietf-webtrans-http3-16 Section 6 のセッション終了
    条件の 1 つ目)。終了後に session_ids_ から削除されるため、終了後の
    send_datagram は送出されない。
    """
    client, _server, session_id = _establish_session()

    # セッション終了前の send_datagram は送出される
    client.send_datagram(session_id, b"before-close")
    assert client.get_datagrams_to_send() == [_encode_wt_datagram(session_id, b"before-close")]

    # CONNECT ストリーム (セッション ID そのもの) をクローズしてセッションを終了する
    assert client.close_stream(session_id, 0) == session_id

    # セッション終了後の send_datagram は送出されない
    client.send_datagram(session_id, b"after-close")
    assert client.get_datagrams_to_send() == []


def test_send_datagram_after_close_session_ignored() -> None:
    """close_session (WT_CLOSE_SESSION 送出) 後に send_datagram が無視されることを確認

    close_session による WT_CLOSE_SESSION 送出はセッション終了の 3 経路の
    1 つ (draft-ietf-webtrans-http3-16 Section 6)。終了後に session_ids_
    から削除されるため、終了後の send_datagram は送出されない。
    """
    client, _server, session_id = _establish_session()

    # セッション終了前の send_datagram は送出される
    client.send_datagram(session_id, b"before-close")
    assert client.get_datagrams_to_send() == [_encode_wt_datagram(session_id, b"before-close")]

    # WT_CLOSE_SESSION を送出してセッションを終了する
    client.close_session(session_id, 0)

    # セッション終了後の send_datagram は送出されない
    client.send_datagram(session_id, b"after-close")
    assert client.get_datagrams_to_send() == []


def test_send_datagram_after_recv_wt_close_session_ignored() -> None:
    """WT_CLOSE_SESSION 受信後に send_datagram が無視されることを確認

    recv_wt_close_session_cb による WT_CLOSE_SESSION 受信はセッション終了
    の 3 経路の 1 つ (draft-ietf-webtrans-http3-16 Section 6)。受信側
    (サーバー) の session_ids_ から削除されるため、終了後の send_datagram
    は送出されない。
    """
    client, server, session_id = _establish_session()

    # セッション終了前の send_datagram は送出される
    server.send_datagram(session_id, b"before-close")
    assert server.get_datagrams_to_send() == [_encode_wt_datagram(session_id, b"before-close")]

    # クライアントが WT_CLOSE_SESSION を送出し、サーバーが受信して
    # セッション終了を検知する
    client.close_session(session_id, 0)
    _pump(client, server)

    # サーバー側で SessionClosed が発火している
    event = server.next_event()
    assert event is not None
    assert event.type == h3.EventType.SESSION_CLOSED
    assert event.session_id == session_id

    # セッション終了後の send_datagram は送出されない
    server.send_datagram(session_id, b"after-close")
    assert server.get_datagrams_to_send() == []


def test_send_datagram_alive_session_delivered() -> None:
    """生存セッションの send_datagram は従来どおり送出されることを確認

    セッション終了後の送信禁止 (draft-ietf-webtrans-http3-16 Section 6 の
    MUST) は終了したセッションにのみ適用され、生存セッションへの送信は
    影響を受けない。
    """
    client, server, session_id = _establish_session()

    # 送信側 (クライアント) でデータグラムが送出される
    client.send_datagram(session_id, b"hello")
    datagrams = client.get_datagrams_to_send()
    assert datagrams == [_encode_wt_datagram(session_id, b"hello")]

    # ピア (サーバー) に届いて Datagram イベントになる
    for datagram in datagrams:
        server.receive_datagram(datagram)
    event = server.next_event()
    assert event is not None
    assert event.type == h3.EventType.DATAGRAM
    assert event.session_id == session_id
    assert event.data == b"hello"


def test_send_datagram_alive_after_other_session_closed_delivered() -> None:
    """一方のセッション終了後も、生存セッションへの送信は継続されることを確認

    セッション終了の MUST (draft-ietf-webtrans-http3-16 Section 6) は終了した
    セッション ID 宛ての送信を禁止するものであり、同一接続の他の生存
    セッションへの送信は影響を受けない。
    """
    client, server, first_session_id, second_session_id = _establish_two_sessions()

    # 1 つ目のセッションを終了する
    client.close_session(first_session_id, 0)

    # 生存セッション (2 つ目) への送信は従来どおり送出される
    client.send_datagram(second_session_id, b"alive")
    datagrams = client.get_datagrams_to_send()
    assert datagrams == [_encode_wt_datagram(second_session_id, b"alive")]

    # ピアに届いて Datagram イベントになる
    for datagram in datagrams:
        server.receive_datagram(datagram)
    event = server.next_event()
    assert event is not None
    assert event.type == h3.EventType.DATAGRAM
    assert event.session_id == second_session_id
    assert event.data == b"alive"


def test_send_datagram_unestablished_session_id_ignored() -> None:
    """一度も確立されていないセッション ID への送信が無視されることを確認

    メンバーシップ確認は終了したセッション ID に限らず、一度も確立されて
    いないセッション ID への送信も無視する (低レベル API の意味論の変更)。
    """
    client, _server, session_id = _establish_session()

    # 確立済み ID とは異なる、一度も確立されていない ID への送信は無視される
    unestablished_session_id = session_id + 4
    client.send_datagram(unestablished_session_id, b"never-established")
    assert client.get_datagrams_to_send() == []


@pytest.mark.parametrize(
    "connect_stream_id",
    [256, 65532, 65536, 1 << 30, 1 << 32],
    ids=[
        "quarter_64_2byte_varint",
        "quarter_16383_2byte_varint",
        "quarter_16384_4byte_varint",
        "quarter_2_28_4byte_varint",
        "quarter_2_30_8byte_varint",
    ],
)
def test_send_datagram_large_session_id_delivered(connect_stream_id: int) -> None:
    """大きなセッション ID への送信で多バイト varint が正しくエンコードされることを確認

    Quarter Stream ID が 2 バイト / 4 バイト / 8 バイト varint (RFC 9000
    可変長整数) になる大きなセッション ID でも、ワイヤ形式が正しく
    エンコードされてピアに届くことを検証する。
    """
    client, server = _create_session_pair()
    # 大きな CONNECT ストリーム ID (4 の倍数) でセッションを確立する
    large_session_id = _connect_session(client, server, connect_stream_id)

    # 大きなセッション ID への送信が送出される
    client.send_datagram(large_session_id, b"large")
    datagrams = client.get_datagrams_to_send()
    assert datagrams == [_encode_wt_datagram(large_session_id, b"large")]

    # ピアに届いて Datagram イベントになる
    for datagram in datagrams:
        server.receive_datagram(datagram)
    event = server.next_event()
    assert event is not None
    assert event.type == h3.EventType.DATAGRAM
    assert event.session_id == large_session_id
    assert event.data == b"large"


def test_send_datagram_optimistic_delivered() -> None:
    """サーバー応答前の楽観的データグラム送信が妨げられないことを確認

    draft-ietf-webtrans-http3-16 Section 4 の楽観的送信: クライアントは
    CONNECT リクエスト送信後・サーバー応答前にデータグラムを送信できる。
    connect 直後に session_ids_ へ挿入されるため、メンバーシップ確認を
    通過して送出される。
    """
    client, _server = _create_session_pair()

    # CONNECT リクエストを送信する (サーバー応答はまだ)
    assert client.connect(0, "https://localhost/webtransport") is True

    # サーバー応答前でもデータグラムは送出される
    client.send_datagram(0, b"optimistic")
    assert client.get_datagrams_to_send() == [_encode_wt_datagram(0, b"optimistic")]


def test_send_datagram_server_optimistic_delivered() -> None:
    """サーバー側の楽観的データグラム送信が妨げられないことを確認

    draft-ietf-webtrans-http3-16 Section 4 の「On the server side, opening
    streams and sending datagrams is possible as soon as the CONNECT
    request has been received」。サーバーは CONNECT リクエスト受信時
    (end_headers_cb) に session_ids_ へ挿入されるため、受理前でも
    データグラムは送出される。
    """
    client, server = _create_session_pair()

    # クライアントが CONNECT を送信し、サーバーがリクエストを受信する (受理前)
    assert client.connect(0, "https://localhost/webtransport") is True
    _pump(client, server)

    # サーバー側で SESSION_READY が発火している (accept_session は未実施)
    event = server.next_event()
    assert event is not None
    assert event.type == h3.EventType.SESSION_READY
    assert event.session_id == 0

    # 受理前でもデータグラムは送出される
    server.send_datagram(0, b"server-optimistic")
    assert server.get_datagrams_to_send() == [_encode_wt_datagram(0, b"server-optimistic")]
