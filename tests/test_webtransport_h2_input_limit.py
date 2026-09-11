"""WebTransport over HTTP/2 の Python 境界入力サイズ上限テスト

nb::bytes から std::vector へコピーする Python 境界の 3 経路
(receive / send_stream_data / send_datagram) は、生の入力バイト数が
1 MiB を超えると C++ 側で std::invalid_argument (ValueError) になる。
上限判定は > のため 1 MiB ちょうどは通り、既定のカプセルペイロード上限と
同値のローカル防御である。
"""

from __future__ import annotations

import pytest
from conftest import _connect_h2_session, _create_h2_session_pair, _h2_pump

_ONE_MIB = 1024 * 1024


def test_receive_rejects_input_over_one_mib() -> None:
    """receive が 1 MiB 超の入力で ValueError になり、イベントを積まないことを確認

    1 MiB + 1 は上限判定が > のため必ず拒否される最小の入力である。検査は
    nghttp2 へ渡す前に走るため、入力由来の Error イベントも発生しない。
    """
    client, server = _create_h2_session_pair()
    _connect_h2_session(client, server)

    with pytest.raises(
        ValueError,
        match=r"receive data must be at most 1048576 bytes: got 1048577",
    ):
        server.receive(b"x" * (_ONE_MIB + 1))
    # 検査失敗時は nghttp2 に渡らないためイベントも積まれない
    assert server.next_event() is None


def test_send_stream_data_rejects_input_over_one_mib() -> None:
    """send_stream_data が 1 MiB 超の入力で ValueError になり、ワイヤへ積まないことを確認"""
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    # 確立時に client がキューした初期フロー制御カプセルを先に送出しておく
    _h2_pump(client, server)

    with pytest.raises(
        ValueError,
        match=r"send_stream_data data must be at most 1048576 bytes: got 1048577",
    ):
        client.send_stream_data(session_id, stream_id, b"x" * (_ONE_MIB + 1))
    # 検査失敗時は WT_STREAM capsule が積まれない (送信待ちなし)
    assert client.send() is None


def test_send_datagram_rejects_input_over_one_mib() -> None:
    """send_datagram が 1 MiB 超の入力で ValueError になり、ワイヤへ積まないことを確認"""
    client, _server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, _server)
    # 確立時に client がキューした初期フロー制御カプセルを先に送出しておく
    _h2_pump(client, _server)

    with pytest.raises(
        ValueError,
        match=r"send_datagram data must be at most 1048576 bytes: got 1048577",
    ):
        client.send_datagram(session_id, b"x" * (_ONE_MIB + 1))
    # 検査失敗時は DATAGRAM capsule が積まれない (送信待ちなし)
    assert client.send() is None


def test_input_check_precedes_session_state_check() -> None:
    """終了済みセッションでも入力検査が先に走り ValueError になることを確認

    binding 層の入力検査は H2Session のセッション状態を見る前に実行される
    ため、close_session 後でも 1 MiB 超は黙殺されず ValueError になる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    client.close_session(session_id, 0)

    with pytest.raises(ValueError, match=r"send_datagram data must be"):
        client.send_datagram(session_id, b"x" * (_ONE_MIB + 1))
    with pytest.raises(ValueError, match=r"send_stream_data data must be"):
        client.send_stream_data(session_id, 0, b"x" * (_ONE_MIB + 1))


def test_receive_accepts_one_mib() -> None:
    """receive が 1 MiB ちょうどの入力を通すことを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。1 MiB ちょうどのデータはローカル検査を通って nghttp2 に渡る
    (解釈結果は問わない)。
    """
    client, server = _create_h2_session_pair()
    _connect_h2_session(client, server)

    # 1 MiB ちょうどは ValueError にならない
    server.receive(b"x" * _ONE_MIB)


def test_send_datagram_accepts_one_mib() -> None:
    """send_datagram が 1 MiB ちょうどの入力を通すことを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。1 MiB はフロー制御クレジットを超え得るため、データが送信待ちに
    積まれること (want_write が真) まで表明する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    # 確立時に client がキューした初期フロー制御カプセルを先に送出しておく
    _h2_pump(client, server)
    assert client.want_write() is False

    client.send_datagram(session_id, b"x" * _ONE_MIB)
    assert client.want_write() is True


def test_send_stream_data_accepts_one_mib() -> None:
    """send_stream_data が 1 MiB ちょうどの入力を通すことを確認

    上限判定が >= に退行すると 1 MiB ちょうどが拒否されるため、境界の有効側を
    固定する。1 MiB はフロー制御クレジットを超え得るため、データが送信待ちに
    積まれること (want_write が真) まで表明する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    # 確立時に client がキューした初期フロー制御カプセルを先に送出しておく
    _h2_pump(client, server)
    assert client.want_write() is False

    client.send_stream_data(session_id, stream_id, b"x" * _ONE_MIB)
    assert client.want_write() is True
