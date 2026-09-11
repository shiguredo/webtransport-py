"""WebTransport over HTTP/2 の reset_stream / stop_sending 入力検証テスト

reset_stream の Reliable Size は送信済みバイト数と一致しなければならない
(draft-ietf-webtrans-http2-15 Section 6.2) ため、任意指定の引数を廃止して
常に bytes_sent を載せる。あわせて varint 範囲 (2^62 - 1、RFC 9000 Section
16) の検査と、存在しないストリーム ID への送出抑止を検証する。
"""

from __future__ import annotations

import pytest
from conftest import (
    _connect_h2_session,
    _create_h2_session_pair,
    _drain_events,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2


def _encode_wt_reset_stream_capsule(stream_id: int, error_code: int, reliable_size: int) -> bytes:
    """WT_RESET_STREAM capsule のワイヤバイト列を組み立てる

    Type 0x190B4D39 (4 バイト varint) + Length + Stream ID + Error Code +
    Reliable Size。Length は 1 バイト varint のみ対応する (小さい値のみ)。
    """
    payload = _encode_varint(stream_id) + _encode_varint(error_code) + _encode_varint(reliable_size)
    assert len(payload) < 0x40, "Length が 1 バイト varint に収まる前提が崩れています"
    return bytes([0x99, 0x0B, 0x4D, 0x39, len(payload)]) + payload


def _encode_wt_stream_capsule(stream_id: int, data: bytes) -> bytes:
    """WT_STREAM capsule のワイヤバイト列を組み立てる

    Type 0x190B4D3C (4 バイト varint) + Length + Stream ID + Stream Data。
    Length は 1 バイト varint のみ対応する (小さい値のみ)。
    """
    payload = _encode_varint(stream_id) + data
    assert len(payload) < 0x40, "Length が 1 バイト varint に収まる前提が崩れています"
    return bytes([0x99, 0x0B, 0x4D, 0x3C, len(payload)]) + payload


def test_reset_stream_uses_bytes_sent_as_reliable_size() -> None:
    """reset_stream の Reliable Size が送信済みバイト数になることを確認

    データ送信後の reset_stream は引数を持たず、常に bytes_sent (= 5) を
    Reliable Size に載せる (draft-15 Section 6.2)。ピアも対称の値として受理
    し、STREAM_RESET イベントが発火する (セッションエラーにならない)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    client.send_stream_data(session_id, stream_id, b"hello")
    _h2_pump(client, server)

    # reliable_size 引数は廃止されている (4 引数呼び出しは TypeError)
    with pytest.raises(TypeError):
        client.reset_stream(session_id, stream_id, 42, 5)

    # 3 引数で reset_stream を呼ぶ (reliable_size は引数に存在しない)
    client.reset_stream(session_id, stream_id, 42)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_reset_stream_capsule(stream_id, 42, 5) in wire

    # wire をピアへ渡し、reliable_size の一致検証で受理されることを確認する
    assert server.receive(wire) > 0
    events = _drain_events(server)
    assert all(event.type != h2.EventType.ERROR for event in events)
    reset_events = [event for event in events if event.type == h2.EventType.STREAM_RESET]
    assert len(reset_events) == 1
    assert reset_events[0].stream_id == stream_id
    assert reset_events[0].error_code == 42


def test_reset_stream_multiple_chunks_uses_total_bytes_sent() -> None:
    """複数チャンク送信後の Reliable Size が合計送信バイト数になることを確認

    3 バイト + 2 バイトの 2 チャンクを送信した後の reset_stream は、合計の
    5 バイトを Reliable Size に載せる (draft-15 Section 6.2 の送信済み
    バイト数)。ピアの bytes_received と一致し STREAM_RESET が発火する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    client.send_stream_data(session_id, stream_id, b"abc")
    client.send_stream_data(session_id, stream_id, b"de")
    _h2_pump(client, server)

    client.reset_stream(session_id, stream_id, 7)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_reset_stream_capsule(stream_id, 7, 5) in wire

    # ピアは合計 5 バイトと一致するため受理する (セッションエラーにならない)
    assert server.receive(wire) > 0
    events = _drain_events(server)
    assert all(event.type != h2.EventType.ERROR for event in events)
    reset_events = [event for event in events if event.type == h2.EventType.STREAM_RESET]
    assert len(reset_events) == 1
    assert reset_events[0].error_code == 7


def test_reset_stream_with_pending_send_uses_sent_bytes_only() -> None:
    """クレジット不足で保留中のデータがある場合、送信済みバイト数のみが Reliable Size になることを確認

    サーバーの初期クレジットを 3 バイトに絞ると、5 バイトの送信は 3 バイトだけが
    ワイヤに載り 2 バイトは保留になる。この状態の reset_stream は、アプリが
    渡した合計 5 ではなく実際に送信済みの 3 を Reliable Size に載せる
    (draft-15 Section 6.2 の送信済みバイト数)。ピアの受信バイト数と一致し
    STREAM_RESET が発火する。
    """
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_initial_max_data = 3
    server_config.wt_initial_max_stream_data = 3
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # 5 バイトを送るが、クレジットは 3 バイトのため 2 バイトは保留になる
    client.send_stream_data(session_id, stream_id, b"abcde")
    wire = client.send()
    assert wire is not None
    assert _encode_wt_stream_capsule(stream_id, b"abc") in wire
    assert server.receive(wire) > 0

    # 保留を抱えたまま reset_stream すると送信済みの 3 バイトが載る
    client.reset_stream(session_id, stream_id, 9)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_reset_stream_capsule(stream_id, 9, 3) in wire
    assert server.receive(wire) > 0
    events = _drain_events(server)
    assert all(event.type != h2.EventType.ERROR for event in events)
    reset_events = [event for event in events if event.type == h2.EventType.STREAM_RESET]
    assert len(reset_events) == 1
    assert reset_events[0].error_code == 9


@pytest.mark.parametrize(
    "stream_id",
    [2**62, 2**63, 2**64 - 1],
    ids=["two_pow_62", "two_pow_63", "two_pow_64_minus_1"],
)
def test_reset_stream_and_stop_sending_over_varint_range_raises_value_error(
    stream_id: int,
) -> None:
    """varint 範囲外 (2^62 以上) の stream_id が ValueError になることを確認

    RFC 9000 Section 16 の上限は 2^62 - 1。超過をエンコードすると上位ビットが
    壊れ別のストリームをリセットするため、reset_stream / stop_sending の両方で
    入力検証により拒否する。例外は副作用の前に送出される。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    # 確立時に client がキューした初期フロー制御カプセルを先に送出しておく
    _h2_pump(client, server)

    with pytest.raises(ValueError, match=r"reset_stream stream_id must be less than 2\^62"):
        client.reset_stream(session_id, stream_id, 0)
    with pytest.raises(ValueError, match=r"stop_sending stream_id must be less than 2\^62"):
        client.stop_sending(session_id, stream_id, 0)

    # 例外時はワイヤにカプセルが積まれない
    wire = client.send()
    assert wire is None


def test_reset_stream_and_stop_sending_over_varint_range_on_closed_session_raises() -> None:
    """終了済みセッションでも入力検証が先に走ることを確認

    入力検証はセッション・未知ストリームの検査より先に行うため、
    close_session 後でも 2^62 以上は ValueError になる (検査順序の退行を
    検出する)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    client.close_session(session_id, 0)

    with pytest.raises(ValueError, match=r"reset_stream stream_id"):
        client.reset_stream(session_id, 2**62, 0)
    with pytest.raises(ValueError, match=r"stop_sending stream_id"):
        client.stop_sending(session_id, 2**62, 0)


@pytest.mark.parametrize(
    "unknown_stream_id",
    [1, 3, 8, 10, 2**62 - 1],
    ids=["server_bidi", "server_uni", "client_bidi", "client_uni", "varint_max"],
)
def test_reset_stream_and_stop_sending_unknown_stream_id_ignored(unknown_stream_id: int) -> None:
    """存在しないストリーム ID への reset_stream / stop_sending が送出されないことを確認

    存在確認を送出可否の条件にし、未知のストリームには黙って何も送出しない
    (セッションは閉じない)。双方向・単方向・ピア開始の各 ID と varint の
    境界値 (2^62 - 1) を対象にする。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    # 確立時に client がキューした初期フロー制御カプセルを先に送出しておく
    _h2_pump(client, server)

    client.reset_stream(session_id, unknown_stream_id, 42)
    client.stop_sending(session_id, unknown_stream_id, 42)

    wire = client.send()
    assert wire is None
    assert client.is_closed() is False
    # WebTransport セッションが生存している (is_closed は接続全体のフラグの
    # ため、セッションの生存は get_session_ids で確認する)
    assert client.get_session_ids() == [session_id]
