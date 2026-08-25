"""WebTransport over HTTP/2 の初期フロー制御 0 フォールバック除去のテスト

draft-15 Section 11.2 の「対向 SETTINGS が 0 (既定値) の場合、
WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS カプセル到着まで送信不可」
(Section 6.5 / 6.6 / 6.7 は非 0 値での MAY 伝達) に従い、0 を広告するピアへの
送信はフォールバック (自側 config 値での送信) を行わない。送信を試みた場合は
既存のフロー制御ガードで WT_FLOW_CONTROL_ERROR、ストリーム開設を試みた場合は
open_stream が -1 を返す。カプセル受信で送信クレジットが前進することを
ワイヤ注入で検証する。
"""

from __future__ import annotations

from conftest import (
    _connect_h2_session,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2

_WT_MAX_DATA = 0x190B4D3D
_WT_MAX_STREAM_DATA = 0x190B4D3E
_WT_MAX_STREAMS_BIDI = 0x190B4D3F
_WT_MAX_STREAMS_UNI = 0x190B4D40


def _encode_capsule(capsule_type: int, payload: bytes) -> bytes:
    """Type / Length / Payload のカプセルバイト列を組み立てる"""
    return _encode_varint(capsule_type) + _encode_varint(len(payload)) + payload


def _encode_data_frame(session_id: int, payload: bytes) -> bytes:
    """DATA フレームのワイヤバイト列を組み立てる

    ピアからのカプセルを注入するために使う。HTTP/2 DATA フレームのペイロード
    はカプセルバイト列そのもののため、フレームヘッダー + カプセルで注入できる。
    """
    return (
        len(payload).to_bytes(3, "big")
        + bytes([0x00, 0x00])
        + (session_id & 0x7FFFFFFF).to_bytes(4, "big")
        + payload
    )


def _encode_wt_close_session_capsule(error_code: int, error_message: str) -> bytes:
    """WT_CLOSE_SESSION capsule のワイヤバイト列を組み立てる

    Type 0x2843 は 2 バイト varint [0x68, 0x43] + Length + Application Error
    Code (32bit) + Message。0x50 は WT_FLOW_CONTROL_ERROR (draft-15
    Section 3.4 の 0xTBD) のプレースホルダ。
    """
    payload = error_code.to_bytes(4, "big") + error_message.encode("utf-8")
    assert len(payload) < 0x40, "Length が 1 バイト varint に収まる前提が崩れています"
    return b"\x68\x43" + bytes([len(payload)]) + payload


def _create_h2_session_pair_with_limits(
    server_max_data: int,
    server_max_stream_data: int,
    server_max_streams_bidi: int,
    server_max_streams_uni: int,
) -> tuple[h2.Session, h2.Session]:
    """サーバー側の初期フロー制御広告値を指定したペアを作成する

    サーバーの config 値はクライアント宛の SETTINGS / WebTransport-Init に
    反映され、クライアントの送信クレジット・ストリーム上限となる
    (draft-15 Section 4.3.1)。
    """
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_initial_max_data = server_max_data
    server_config.wt_initial_max_stream_data = server_max_stream_data
    server_config.wt_initial_max_streams_bidi = server_max_streams_bidi
    server_config.wt_initial_max_streams_uni = server_max_streams_uni
    server = h2.Session.create_server(server_config)

    # クライアントの preface + SETTINGS をサーバーへ
    _h2_pump(client, server)
    # サーバーの SETTINGS をクライアントへ
    _h2_pump(server, client)

    return client, server


def _inject_capsule(session: h2.Session, session_id: int, capsule: bytes) -> None:
    """ワイヤ注入でカプセルをセッションへ送る"""
    ret = session.receive(_encode_data_frame(session_id, capsule))
    assert ret > 0, "カプセルの注入に失敗しました"


def test_zero_peer_initial_limits_block_until_capsules() -> None:
    """対向 SETTINGS が 0 の場合、カプセル受信まで送信できないことを確認

    サーバーが 0 の初期フロー制御 (WT_MAX_DATA / WT_MAX_STREAM_DATA /
    WT_MAX_STREAMS) を広告したセッションでは、クライアントの送信クレジットと
    ストリーム上限は 0 になる。修正前は自側 config 値へフォールバックし、
    広告制限を超える送信が可能だった (draft-15 Section 6.5 / 6.6 / 6.7 の
    MUST 違反)。WT_MAX_STREAMS_BIDI / WT_MAX_STREAM_DATA (Stream ID + 値の
    形式) / WT_MAX_DATA カプセルの受信で前進することを検証する。
    """
    client, server = _create_h2_session_pair_with_limits(0, 0, 0, 0)
    session_id = _connect_h2_session(client, server)

    # ストリームクレジット 0: open_stream は -1 を返す
    assert client.open_stream(session_id, False) == -1

    # WT_MAX_STREAMS_BIDI カプセル受信でストリーム上限が前進する
    capsule = _encode_capsule(_WT_MAX_STREAMS_BIDI, _encode_varint(100))
    _inject_capsule(client, session_id, capsule)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0, "WT_MAX_STREAMS_BIDI 受信後もストリームを開けません"

    # WT_MAX_STREAM_DATA (Stream ID + Maximum Stream Data) カプセル受信で
    # ストリームレベルの送信クレジットが前進する
    capsule = _encode_capsule(_WT_MAX_STREAM_DATA, _encode_varint(stream_id) + _encode_varint(1024))
    _inject_capsule(client, session_id, capsule)

    # WT_MAX_DATA カプセル受信でセッションレベルの送信クレジットが前進し、
    # データが送出される
    capsule = _encode_capsule(_WT_MAX_DATA, _encode_varint(1024))
    _inject_capsule(client, session_id, capsule)
    client.send_stream_data(session_id, stream_id, b"hello")
    wire = client.send()
    assert wire is not None
    assert b"hello" in wire


def test_zero_peer_initial_uni_streams_limit_block_until_capsule() -> None:
    """対向の単方向ストリーム上限が 0 の場合、カプセル受信まで開けないことを確認

    単方向ストリームの初期上限 (WT_MAX_STREAMS, UNI) を 0 で広告された
    セッションでは、open_stream(True) は -1 を返す。WT_MAX_STREAMS_UNI
    カプセルの受信で上限が前進することを検証する (draft-15 Section 6.7)。
    """
    client, server = _create_h2_session_pair_with_limits(1024, 1024, 100, 0)
    session_id = _connect_h2_session(client, server)

    # 単方向ストリームクレジット 0: open_stream(True) は -1 を返す
    assert client.open_stream(session_id, True) == -1

    # WT_MAX_STREAMS_UNI カプセル受信で単方向ストリーム上限が前進する
    capsule = _encode_capsule(_WT_MAX_STREAMS_UNI, _encode_varint(100))
    _inject_capsule(client, session_id, capsule)
    stream_id = client.open_stream(session_id, True)
    assert stream_id >= 0, "WT_MAX_STREAMS_UNI 受信後も単方向ストリームを開けません"


def test_zero_peer_initial_data_limit_send_raises_flow_control_error() -> None:
    """対向 SETTINGS のデータクレジットが 0 の場合の送信試行で WT_FLOW_CONTROL_ERROR になることを確認

    ストリーム数上限が通常値 (100) のまま、データクレジットのみ 0 を広告
    されたセッションでは open_stream は成功するが、send_stream_data が
    既存のフロー制御ガード (draft-15 Section 6.5 / 6.6) で
    WT_FLOW_CONTROL_ERROR (0x50) のセッションクローズになる。
    フォールバックで送信できてしまう (修正前) に対するピン。
    """
    client, server = _create_h2_session_pair_with_limits(0, 1024, 100, 100)
    session_id = _connect_h2_session(client, server)

    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    client.send_stream_data(session_id, stream_id, b"hello")
    wire = client.send()
    assert wire is not None
    assert _encode_wt_close_session_capsule(0x50, "flow control limit exceeded") in wire
