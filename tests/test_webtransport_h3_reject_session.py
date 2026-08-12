"""WebTransport over HTTP/3 の非 2xx 応答受信時のセッション ID 削除テスト

サーバーが CONNECT リクエストを拒否 (非 2xx 応答) した場合、クライアントの
session_ids_ にセッション ID が残り続け、拒否されたセッション ID 宛の
send_datagram がデータグラムを送出してしまう問題の修正を検証する。
nghttp3 は非 2xx 応答を受信した CONNECT ストリームを reset するため
end_stream コールバックが発火せず、既存の FIN 経路では削除されない。
"""

from __future__ import annotations

import pytest
from conftest import _connect_session, _create_session_pair, _pump, _setup_connect

from webtransport import h3


def _drain_events(session: h3.Session) -> list[h3.Event]:
    """セッションに積まれたイベントを全て取り出す"""
    events = []
    while True:
        event = session.next_event()
        if event is None:
            break
        events.append(event)
    return events


@pytest.mark.parametrize(
    "status_code",
    [403, 302, 500],
    ids=["forbidden", "redirect", "server_error"],
)
def test_client_non_2xx_response_removes_session_id(status_code: int) -> None:
    """非 2xx 応答でクライアントの session_ids_ からセッション ID が削除されることを確認

    サーバーが CONNECT リクエストを非 2xx (403 / 302 / 500) で拒否した場合、
    クライアントの session_ids_ にセッション ID が残り続けるのが問題であり、
    非 2xx 応答受信時に削除されることを検証する (draft-ietf-webtrans-http3-16
    Section 3.2)。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)
    assert server.get_session_ids() == [0]

    # サーバーが非 2xx で拒否する
    server.reject_session(0, status_code)
    _pump(server, client)

    # クライアントの session_ids_ から削除されている
    assert client.get_session_ids() == []


@pytest.mark.parametrize(
    "status_code",
    [403, 302, 500],
    ids=["forbidden", "redirect", "server_error"],
)
def test_client_non_2xx_response_send_datagram_ignored(status_code: int) -> None:
    """拒否されたセッション ID 宛の send_datagram がデータグラムを送出しないことを確認

    拒否されたセッションは確立されていない (draft-ietf-webtrans-http3-16
    Section 3.2) ため、楽観的送信 (Section 4) の枠を超えて送信が継続する
    経路を塞ぐ。削除後は session_ids_ のメンバーシップ確認が成立しないため、
    send_datagram は黙って無視される。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)

    # サーバーが非 2xx で拒否する
    server.reject_session(0, status_code)
    _pump(server, client)

    # 拒否後の send_datagram は送出されない
    client.send_datagram(0, b"after-reject")
    assert client.get_datagrams_to_send() == []


@pytest.mark.parametrize(
    "status_code",
    [403, 302, 500],
    ids=["forbidden", "redirect", "server_error"],
)
def test_client_non_2xx_response_open_stream_fails(status_code: int) -> None:
    """拒否されたセッション ID 宛の open_stream が失敗することを確認

    拒否後に session_ids_ から削除されるため、open_stream のメンバーシップ
    確認が成立せず false を返す (draft-ietf-webtrans-http3-16 Section 6 の
    MUST)。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)

    # 拒否前の楽観的オープンは成功する (draft-ietf-webtrans-http3-16
    # Section 4 の楽観的オープン。nghttp3 は応答受信前でも wt.session の
    # 存在のみで open を許容する)。スケジューラに残らないよう書き出して
    # から次に進む
    assert client.open_stream(0, 4, False) is True
    client.get_streams_to_send()

    # サーバーが非 2xx で拒否する
    server.reject_session(0, status_code)
    _pump(server, client)

    # 拒否後の open_stream は失敗する
    assert client.open_stream(0, 4, False) is False


@pytest.mark.parametrize(
    "status_code",
    [403, 302, 500],
    ids=["forbidden", "redirect", "server_error"],
)
def test_client_non_2xx_response_no_session_closed_event(status_code: int) -> None:
    """拒否されたセッションに対して SessionClosed イベントが発火しないことを確認

    非 2xx 応答ではセッションは一度も確立されていない
    (draft-ietf-webtrans-http3-16 Section 3.2) ため、SessionClosed
    (セッション終了の通知) の意味論が合わない。黙って削除する。
    本テストは修正前実装でも通る設計ピンであり、SessionClosed 不発火の
    意味論を守る (nghttp3 の abort_stream 由来の RESET_STREAM /
    STOP_SENDING イベントは QUIC 層への通知のため発火する)。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)

    # サーバーが非 2xx で拒否する
    server.reject_session(0, status_code)
    _pump(server, client)

    # SessionClosed は発火しない
    assert all(event.type != h3.EventType.SESSION_CLOSED for event in _drain_events(client))


def test_client_response_103_session_removed() -> None:
    """1xx 中間応答でもセッション ID が削除されることを確認

    draft-ietf-webtrans-http3-16 Section 3.2 では 2xx のみがセッション確立
    であり、1xx は確立応答ではない。現在の依存 nghttp3 は 1xx 中間応答を
    受信すると status_code を -1 へ戻して非 2xx として CONNECT ストリーム
    を abort する (nghttp3 が 1xx を中間応答として扱う更新が入った場合は
    この挙動の見直しが必要)。本テストは修正前実装では失敗する (修正前は
    1xx で何も起きずセッション ID が残る) ため、1xx が削除経路に含まれる
    ことを検証する。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)

    # サーバーが 103 (1xx 中間応答) で応答する
    server.reject_session(0, 103)
    _pump(server, client)

    # 1xx でもセッション ID は削除され、SessionClosed は発火しない
    assert client.get_session_ids() == []
    assert all(event.type != h3.EventType.SESSION_CLOSED for event in _drain_events(client))


def test_client_response_201_session_kept_until_fin() -> None:
    """2xx 非 200 応答 (201) のセッションが誤って削除されないことを確認

    nghttp3 は 2xx 全般をセッション確立として扱う (status_code / 100 == 2
    による confirm) ため、201 応答は有効なセッションである。session_ids_
    に残ること・SESSION_READY が発火しないこと (200 のみ発火する既存の
    制約) を確認し、FIN 到着後は既存の FIN 経路で後始末されることを確認
    する。本テストは修正前実装でも通る設計ピンであり、過剰削除の防止を
    守る。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)
    server.receive_stream_data(0, headers, False)

    # サーバーが 201 で応答する (reject_session は任意のステータスコードで
    # 応答を生成できる。accept_session は 200 固定のため 2xx 非 200 は
    # こちらで生成する)
    server.reject_session(0, 201)
    for _ in range(64):
        sent = False
        for stream_id, data, fin in server.get_streams_to_send():
            sent = True
            if stream_id == 0:
                # サーバーが送出する FIN フラグを外してヘッダーのみ届ける
                client.receive_stream_data(stream_id, data, False)
            else:
                client.receive_stream_data(stream_id, data, fin)
        if not sent:
            break

    # 2xx のため削除されず、SESSION_READY も発火しない (既存の制約)。
    # セッションは確立済みとして実用可能 (send_datagram / open_stream が
    # 成功する)
    assert client.get_session_ids() == [0]
    assert all(event.type != h3.EventType.SESSION_READY for event in _drain_events(client))
    client.send_datagram(0, b"alive")
    assert len(client.get_datagrams_to_send()) == 1
    assert client.open_stream(0, 4, False) is True

    # FIN 到着後は既存の FIN 経路 (end_stream コールバック) で後始末される
    client.receive_stream_data(0, b"", True)
    assert client.get_session_ids() == []
    closed_events = [
        event for event in _drain_events(client) if event.type == h3.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == 0


def test_client_accept_200_normal_session_unaffected() -> None:
    """通常のセッション確立 (200 応答) が非 2xx 応答処理の影響を受けないことを確認

    SESSION_READY の発火条件 (200 のみ) は変更しない。本テストは修正前
    実装でも通る回帰ピンであり、通常の確立経路の維持を守る。
    """
    client, server = _create_session_pair()
    session_id = _connect_session(client, server, 0)

    # クライアントで SESSION_READY が発火し、セッションが確立される
    assert client.get_session_ids() == [session_id]
