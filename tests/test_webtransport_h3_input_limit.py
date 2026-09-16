"""WebTransport over HTTP/3 の Python 境界入力サイズ上限テスト

nb::bytes から std::vector へコピーする Python 境界の 4 経路
(receive_stream_data / receive_datagram / send_stream_data / send_datagram) は、
生の入力バイト数が 1 MiB を超えると C++ 側で std::invalid_argument
(ValueError) になる。上限判定は > のため 1 MiB ちょうどは通る。
"""

from __future__ import annotations

import pytest
from conftest import _drain_events, _encode_varint, _establish_session, _pump

from webtransport import h3

_ONE_MIB = 1024 * 1024


def test_receive_stream_data_rejects_input_over_one_mib() -> None:
    """receive_stream_data が 1 MiB 超の入力で ValueError になり、イベントを積まないことを確認

    1 MiB + 1 は上限判定が > のため必ず拒否される最小の入力である。検査は
    nghttp3 へ渡す前に走るため、入力由来のイベントも発生しない。
    """
    _client, server, _session_id = _establish_session()

    with pytest.raises(
        ValueError,
        match=r"receive_stream_data data must be at most 1048576 bytes: got 1048577",
    ):
        server.receive_stream_data(0, b"x" * (_ONE_MIB + 1))
    # 検査失敗時は nghttp3 に渡らないためイベントも積まれない
    assert _drain_events(server) == []


def test_receive_datagram_rejects_input_over_one_mib() -> None:
    """receive_datagram が 1 MiB 超の入力で ValueError になり、イベントを積まないことを確認"""
    _client, server, _session_id = _establish_session()

    with pytest.raises(
        ValueError,
        match=r"receive_datagram data must be at most 1048576 bytes: got 1048577",
    ):
        server.receive_datagram(b"x" * (_ONE_MIB + 1))
    # 検査失敗時は nghttp3 に渡らないためイベントも積まれない
    assert _drain_events(server) == []


def test_send_stream_data_rejects_input_over_one_mib() -> None:
    """send_stream_data が 1 MiB 超の入力で ValueError になり、ワイヤへ積まないことを確認"""
    client, server, session_id = _establish_session()
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    # ストリーム開設で積まれる WT ストリームヘッダを相手へ渡してキューを空にする
    _pump(client, server)

    with pytest.raises(
        ValueError,
        match=r"send_stream_data data must be at most 1048576 bytes: got 1048577",
    ):
        client.send_stream_data(stream_id, b"x" * (_ONE_MIB + 1))
    # 検査失敗時はストリームデータが積まれない (送信待ちなし)
    assert client.get_streams_to_send() == []


def test_send_datagram_rejects_input_over_one_mib() -> None:
    """send_datagram が 1 MiB 超の入力で ValueError になり、ワイヤへ積まないことを確認"""
    client, _server, session_id = _establish_session()

    with pytest.raises(
        ValueError,
        match=r"send_datagram data must be at most 1048576 bytes: got 1048577",
    ):
        client.send_datagram(session_id, b"x" * (_ONE_MIB + 1))
    # 検査失敗時はデータグラムが積まれない (送信待ちなし)
    assert client.get_datagrams_to_send() == []


def test_input_check_precedes_session_state_check() -> None:
    """終了済みセッションでも入力検査が先に走り ValueError になることを確認

    binding 層の入力検査は H3Session のセッション状態を見る前に実行される
    ため、close_session 後でも 1 MiB 超は黙殺されず ValueError になる。
    """
    client, server, session_id = _establish_session()
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    _pump(client, server)
    client.close_session(session_id)

    with pytest.raises(ValueError, match=r"send_datagram data must be"):
        client.send_datagram(session_id, b"x" * (_ONE_MIB + 1))
    with pytest.raises(ValueError, match=r"send_stream_data data must be"):
        client.send_stream_data(stream_id, b"x" * (_ONE_MIB + 1))


def test_receive_stream_data_accepts_one_mib() -> None:
    """receive_stream_data が 1 MiB ちょうどの入力を通すことを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。1 MiB ちょうどのデータはローカル検査を通って nghttp3 に渡り、
    受理バイト数として返る。
    """
    _client, server, _session_id = _establish_session()

    # 1 MiB ちょうどは ValueError にならず、全量が nghttp3 に受理される
    assert server.receive_stream_data(0, b"x" * _ONE_MIB) == _ONE_MIB


def test_receive_datagram_accepts_one_mib() -> None:
    """receive_datagram が 1 MiB ちょうどの入力を通すことを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。受信側の上限は Quarter Stream ID を含むワイヤ長に対する判定で
    あるため、ワイヤ長が 1 MiB ちょうどになるデータグラムを渡し、検査を通って
    ペイロードが DATAGRAM イベントとして届くことまで表明する。
    """
    _client, server, session_id = _establish_session()
    quarter_stream_id = _encode_varint(session_id // 4)
    payload = b"x" * (_ONE_MIB - len(quarter_stream_id))

    server.receive_datagram(quarter_stream_id + payload)
    datagram_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.DATAGRAM
    ]
    assert len(datagram_events) == 1
    assert datagram_events[0].session_id == session_id
    assert datagram_events[0].data == payload


def test_send_stream_data_accepts_one_mib() -> None:
    """send_stream_data が 1 MiB ちょうどの入力を通すことを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。送信キューに積まれることまで表明し、単に例外が出ないだけで
    終わらせない。
    """
    client, server, session_id = _establish_session()
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    # ストリーム開設で積まれる WT ストリームヘッダを相手へ渡してキューを空にする
    _pump(client, server)

    client.send_stream_data(stream_id, b"x" * _ONE_MIB)
    streams = client.get_streams_to_send()
    assert len(streams) == 1
    sent_stream_id, sent_data, _fin = streams[0]
    assert sent_stream_id == stream_id
    assert len(sent_data) == _ONE_MIB


def test_send_datagram_accepts_one_mib() -> None:
    """send_datagram が 1 MiB ちょうどの入力を通すことを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。送信キューに積まれることまで表明し、単に例外が出ないだけで
    終わらせない。
    """
    client, _server, session_id = _establish_session()

    client.send_datagram(session_id, b"x" * _ONE_MIB)
    datagrams = client.get_datagrams_to_send()
    assert len(datagrams) == 1
    # get_datagrams_to_send の戻り値は Quarter Stream ID を前置したワイヤ形式
    # (1 バイトの varint) であるため、ペイロード分は 1 MiB ちょうどになる
    assert datagrams[0][1:] == b"x" * _ONE_MIB
    assert len(datagrams[0]) == _ONE_MIB + 1
