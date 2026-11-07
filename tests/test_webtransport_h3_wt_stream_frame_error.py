"""WebTransport over HTTP/3 の WT_STREAM 誤配置テスト

draft-ietf-webtrans-http3-17 Section 4.3 は、WT_STREAM (双方向ストリームの
シグナル値 0x41) を「双方向ストリームの最初のバイト列」に限り送ってよいと
定め、それ以外で受信した場合は H3_FRAME_UNEXPECTED の接続エラーにすること
を MUST とする。

nghttp3 は WebTransport 有効時の新規双方向ストリームに MAYBE_WT_DATA を
立てるため、HEADERS の後の WT_STREAM を受理してしまう。本テストは
バインディング層が nghttp3 へ渡す前に誤配置を検出し、0x0105 で接続を
閉じることを検証する。
"""

from conftest import _create_session_pair, _drain_events, _establish_session

from webtransport import h3

# WT_STREAM のシグナル値 0x41 の QUIC varint 表現 (2 バイト)
_WT_STREAM_SIGNAL = b"\x40\x41"


def _encode_varint(value: int) -> bytes:
    """RFC 9000 Section 16 の可変長整数をエンコードする"""
    if value < 0x40:
        return bytes([value])
    if value < 0x4000:
        return (value | 0x4000).to_bytes(2, "big")
    if value < 0x40000000:
        return (value | 0x80000000).to_bytes(4, "big")
    return (value | 0xC000000000000000).to_bytes(8, "big")


def _get_headers_frame() -> bytes:
    """通常の GET リクエストの HEADERS フレームを組み立てる

    QPACK 静的テーブル参照だけで構成する (:method GET / :scheme https /
    :path / / :authority localhost)。動的テーブルを使わないため、QPACK
    エンコーダーストリームの転送は不要である。
    """
    block = (
        b"\x00\x00"
        + bytes([0xC0 | 17])  # :method GET
        + bytes([0xC0 | 23])  # :scheme https
        + bytes([0xC0 | 1])  # :path /
        + b"\x50"  # :authority (name reference index 0)
        + bytes([9])
        + b"localhost"
    )
    return b"\x01" + _encode_varint(len(block)) + block


def _wt_stream_frame(payload: bytes = b"") -> bytes:
    """WT_STREAM の誤配置検出に必要な先頭バイト列を組み立てる

    実際の WT_STREAM はシグナルの後にセッション ID が続くが、誤配置の
    判定はシグナルの位置だけで決まる。ここでは観測が長さ varint として
    読むバイト列も付けて、フレームヘッダーとして解釈できる形にする。
    """
    return _WT_STREAM_SIGNAL + _encode_varint(len(payload)) + payload


def _error_codes(events: list[h3.Event]) -> list[int]:
    """Error イベントの error_code だけを取り出す"""
    return [event.error_code for event in events if event.type == h3.EventType.ERROR]


def test_wt_stream_after_headers_is_frame_unexpected() -> None:
    """HEADERS の後の WT_STREAM を H3_FRAME_UNEXPECTED で拒否する"""
    _client, server = _create_session_pair()
    server.receive_stream_data(4, _get_headers_frame(), False)
    _drain_events(server)

    assert server.receive_stream_data(4, _wt_stream_frame(b"xx"), False) == 0

    events = _drain_events(server)
    assert _error_codes(events) == [0x0105], "H3_FRAME_UNEXPECTED が発火していません"
    errors = [event for event in events if event.type == h3.EventType.ERROR]
    assert errors[0].stream_id == 4
    assert errors[0].error_message == "WT_STREAM frame in invalid position"
    assert server.is_closed() is True


def test_wt_stream_after_connect_headers_is_frame_unexpected() -> None:
    """CONNECT ストリームの HEADERS の後でも H3_FRAME_UNEXPECTED で拒否する"""
    _client, server, session_id = _establish_session()

    assert server.receive_stream_data(session_id, _wt_stream_frame(), False) == 0

    events = _drain_events(server)
    assert _error_codes(events) == [0x0105], "H3_FRAME_UNEXPECTED が発火していません"
    assert server.is_closed() is True


def test_wt_stream_as_first_frame_is_allowed() -> None:
    """双方向ストリームの最初のフレームの WT_STREAM は拒否しない"""
    _client, server = _create_session_pair()

    server.receive_stream_data(4, _wt_stream_frame(b"xx"), False)

    events = _drain_events(server)
    assert 0x0105 not in _error_codes(events), "最初のフレームの WT_STREAM を誤って拒否しています"
    assert server.is_closed() is False


def test_wt_stream_after_headers_split_across_chunks_is_frame_unexpected() -> None:
    """WT_STREAM が分割到着しても誤配置を検出する"""
    frame = _wt_stream_frame(b"xx")

    for split in range(1, len(frame)):
        _client, server = _create_session_pair()
        server.receive_stream_data(4, _get_headers_frame(), False)
        _drain_events(server)

        server.receive_stream_data(4, frame[:split], False)
        server.receive_stream_data(4, frame[split:], False)

        events = _drain_events(server)
        assert 0x0105 in _error_codes(events), (
            f"{split} バイト目で分割すると WT_STREAM の誤配置を検出できません"
        )
        assert server.is_closed() is True


def test_data_frame_payload_containing_wt_signal_is_not_misplaced() -> None:
    """DATA フレームのペイロード内の 0x40 0x41 を誤検出しない"""
    _client, server = _create_session_pair()
    server.receive_stream_data(4, _get_headers_frame(), False)
    _drain_events(server)

    payload = b"xx" + _WT_STREAM_SIGNAL + b"yy"
    data_frame = b"\x00" + _encode_varint(len(payload)) + payload
    server.receive_stream_data(4, data_frame, False)

    events = _drain_events(server)
    assert 0x0105 not in _error_codes(events), (
        "DATA フレームのペイロードを WT_STREAM と誤検出しています"
    )
    assert server.is_closed() is False
