"""WebTransport over HTTP/3 のストリーム状態確認 API テスト"""

from __future__ import annotations

from conftest import _establish_session, _pump


def test_stream_writable() -> None:
    """データストリームの書き込み可否が取得できることを確認"""
    client, _server, session_id = _establish_session()

    # データストリームを開くと書き込み可能になる
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    assert client.stream_writable(stream_id) == 1

    # 存在しないストリームは書き込み不可
    assert client.stream_writable(999) == 0


def test_stream_flushed() -> None:
    """送信データが QUIC スタックに受け渡し済みか確認できることを確認"""
    client, server, session_id = _establish_session()

    # データストリームを開いてデータを送信する
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"hello")

    # 送信処理前は QUIC スタックに受け渡し済みでない
    assert client.stream_flushed(stream_id) == 0

    # 送信処理で QUIC スタックに受け渡すと受け渡し済みになる
    _pump(client, server)
    assert client.stream_flushed(stream_id) == 1

    # 存在しないストリームは受け渡し済み扱い (1) になる
    assert client.stream_flushed(999) == 1


def test_stream_wt_session_id() -> None:
    """ストリームが属する WebTransport セッション ID が取得できることを確認"""
    client, _server, session_id = _establish_session()

    # データストリームのセッション ID が取得できる
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    assert client.stream_wt_session_id(stream_id) == session_id

    # 存在しないストリームは None
    assert client.stream_wt_session_id(999) is None

    # WebTransport データストリームでないストリームもセッション ID を持たない
    # ため None。CONNECT ストリーム自身 (セッション ID は CONNECT ストリーム
    # ID そのもの。draft-ietf-webtrans-http3-16 Section 2.2) で検証する
    assert client.stream_wt_session_id(session_id) is None
