"""WebTransport over HTTP/2 のフロー制御カプセル受信値検証テスト

draft-15 Section 6.5 / 6.6 / 6.7 の MUST 「前回受信値より小さい
WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS は
WT_FLOW_CONTROL_ERROR」と、 Section 6.7 / 6.10 の MUST 「Maximum Streams が
2^60 を超える値は WT_FLOW_CONTROL_ERROR」を検証する。不正カプセルは
ワイヤ注入で再現する (公開 API では非コンプライアントな値を送出する
手段が存在しないため)。セッション閉鎖は close_session 経由の
WT_CLOSE_SESSION (error code 0x50) で実現され、ワイヤ部分列チェックで
検証する。0x50 は WT_FLOW_CONTROL_ERROR (0xTBD) のプレースホルダ
(draft-15 Section 3.4)。
"""

from __future__ import annotations

from conftest import (
    _connect_h2_session,
    _create_h2_session_pair,
    _drain_events,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2

# Capsule Type (draft-15 Section 6)
_WT_MAX_DATA = 0x190B4D3D
_WT_MAX_STREAM_DATA = 0x190B4D3E
_WT_MAX_STREAMS_BIDI = 0x190B4D3F
_WT_MAX_STREAMS_UNI = 0x190B4D40
_WT_STREAMS_BLOCKED_BIDI = 0x190B4D43
_WT_STREAMS_BLOCKED_UNI = 0x190B4D44

# Maximum Streams の上限は 2^60
_MAX_STREAMS_LIMIT = 1 << 60


def _encode_capsule(capsule_type: int, payload: bytes) -> bytes:
    """Type / Length / Payload のカプセルバイト列を組み立てる"""
    return _encode_varint(capsule_type) + _encode_varint(len(payload)) + payload


def _encode_data_frame(session_id: int, payload: bytes) -> bytes:
    """DATA フレームのワイヤバイト列を組み立てる

    ピアからのカプセルをサーバーに注入するために使う。HTTP/2 DATA フレーム
    のペイロードはカプセルバイト列そのもののため、フレームヘッダー + カプセル
    で注入できる。
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
    Code (32bit) + Message。Length は 1 バイト varint のみ対応する (テストで
    使う小さい値のみ。64 バイト未満のペイロード前提)。
    """
    payload = error_code.to_bytes(4, "big") + error_message.encode("utf-8")
    assert len(payload) < 0x40, "Length が 1 バイト varint に収まる前提が崩れています"
    return b"\x68\x43" + bytes([len(payload)]) + payload


def _assert_flow_control_error_sent(server: h2.Session, error_message: str) -> None:
    """WT_FLOW_CONTROL_ERROR (0x50) の WT_CLOSE_SESSION が送出されることを確認

    0x50 は draft-15 Section 3.4 の 0xTBD のプレースホルダ。draft で値が
    確定したら更新する。
    """
    wire = server.send()
    assert wire is not None
    assert _encode_wt_close_session_capsule(0x50, error_message) in wire


def _assert_no_flow_control_error_sent(server: h2.Session) -> None:
    """WT_CLOSE_SESSION が送出されないことを確認

    0x68 0x43 は WT_CLOSE_SESSION (Type 0x2843) の 2 バイト varint。
    エラー検知 (close_session 呼び出し) があれば必ずワイヤに現れるため、
    Type の非存在でエラー送出なしを検証できる。
    """
    wire = server.send()
    assert wire is None or b"\x68\x43" not in wire


def _inject_capsule(server: h2.Session, session_id: int, capsule_type: int, payload: bytes) -> None:
    """サーバーへ DATA フレームとしてカプセルを注入する"""
    ret = server.receive(_encode_data_frame(session_id, _encode_capsule(capsule_type, payload)))
    assert ret > 0, "カプセルの注入に失敗しました"


def test_wt_max_data_decrease_closes_session() -> None:
    """前回受信値より小さい WT_MAX_DATA で WT_FLOW_CONTROL_ERROR になることを確認

    対向 SETTINGS の SETTINGS_WT_INITIAL_MAX_DATA (既定 1048576) は受信値
    なので、それより小さいカプセルは Section 6.5 の MUST 違反になる。
    修正前は max() するだけで減少を無視していた。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(server, session_id, _WT_MAX_DATA, _encode_varint(1))
    _assert_flow_control_error_sent(server, "WT_MAX_DATA decreased")


def test_wt_max_stream_data_decrease_closes_session() -> None:
    """前回受信値より小さい WT_MAX_STREAM_DATA で WT_FLOW_CONTROL_ERROR になることを確認

    ストリーム未作成でも SETTINGS_WT_INITIAL_MAX_STREAM_DATA_* (既定 262144)
    は受信値なので、それより小さいカプセルは Section 6.6 の MUST 違反になる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    payload = _encode_varint(0) + _encode_varint(1)
    _inject_capsule(server, session_id, _WT_MAX_STREAM_DATA, payload)
    _assert_flow_control_error_sent(server, "WT_MAX_STREAM_DATA decreased")


def test_wt_max_stream_data_increase_then_decrease_closes_session() -> None:
    """未作成ストリームへの WT_MAX_STREAM_DATA 増加後の減少を検知することを確認

    SETTINGS 既定 262144 より大きい 500000 を受けたあと 400000 を受けると、
    カプセル同士の減少として Section 6.6 の MUST 違反になる。増加値を
    捨てると 400000 は SETTINGS より大きくエラーにならない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(
        server, session_id, _WT_MAX_STREAM_DATA, _encode_varint(0) + _encode_varint(500000)
    )
    _assert_no_flow_control_error_sent(server)

    _inject_capsule(
        server, session_id, _WT_MAX_STREAM_DATA, _encode_varint(0) + _encode_varint(400000)
    )
    _assert_flow_control_error_sent(server, "WT_MAX_STREAM_DATA decreased")


def test_wt_max_stream_data_increase_raises_send_credit() -> None:
    """未作成ストリームへの WT_MAX_STREAM_DATA 増加が送信上限に乗ることを確認

    SETTINGS 既定 262144 より大きい 500000 を受けたあとストリームを開き、
    既定を超える 262145 バイトを送ってもセッションは閉じない。増加を
    クレジットへ反映しないと flow control limit exceeded になる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(
        server, session_id, _WT_MAX_STREAM_DATA, _encode_varint(0) + _encode_varint(500000)
    )
    _assert_no_flow_control_error_sent(server)

    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0, "ストリームの作成に失敗しました"
    client.send_stream_data(session_id, stream_id, b"x")
    _h2_pump(client, server)
    assert stream_id in server.get_stream_ids(session_id), (
        "サーバーにストリームが作成されていません"
    )

    server.send_stream_data(session_id, stream_id, b"y" * 262145)
    wire = server.send()
    assert wire is not None, "送信クレジットが増えていないため送信できませんでした"
    assert b"\x68\x43" not in wire, "フロー制御超過でセッションが閉じられました"


def test_wt_max_stream_data_increase_on_existing_stream_raises_send_credit() -> None:
    """既存ストリームへの WT_MAX_STREAM_DATA 増加が送信上限に乗ることを確認

    ストリーム作成後に SETTINGS 既定 262144 より大きい 500000 を受けると、
    既定を超える 262145 バイトを送ってもセッションは閉じない。既存
    ストリーム分岐だけクレジットを上げ忘れる回帰を検出する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0, "ストリームの作成に失敗しました"
    client.send_stream_data(session_id, stream_id, b"x")
    _h2_pump(client, server)
    assert stream_id in server.get_stream_ids(session_id), (
        "サーバーにストリームが作成されていません"
    )

    _inject_capsule(
        server, session_id, _WT_MAX_STREAM_DATA, _encode_varint(stream_id) + _encode_varint(500000)
    )
    _assert_no_flow_control_error_sent(server)

    server.send_stream_data(session_id, stream_id, b"y" * 262145)
    wire = server.send()
    assert wire is not None, "送信クレジットが増えていないため送信できませんでした"
    assert b"\x68\x43" not in wire, "フロー制御超過でセッションが閉じられました"


def test_wt_max_stream_data_decrease_on_existing_stream() -> None:
    """既存ストリームへの WT_MAX_STREAM_DATA 減少で WT_FLOW_CONTROL_ERROR になることを確認

    対向が WT_STREAM でストリームを開いたあと、SETTINGS 初期値より小さい
    Maximum Stream Data を受ける経路 (streams エントリあり) を検証する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0, "ストリームの作成に失敗しました"
    client.send_stream_data(session_id, stream_id, b"x")
    _h2_pump(client, server)
    assert stream_id in server.get_stream_ids(session_id), (
        "サーバーにストリームが作成されていません"
    )

    _inject_capsule(
        server, session_id, _WT_MAX_STREAM_DATA, _encode_varint(stream_id) + _encode_varint(1)
    )
    _assert_flow_control_error_sent(server, "WT_MAX_STREAM_DATA decreased")


def test_wt_max_streams_bidi_decrease_closes_session() -> None:
    """前回受信値より小さい WT_MAX_STREAMS (bidi) で WT_FLOW_CONTROL_ERROR になることを確認

    対向 SETTINGS の SETTINGS_WT_INITIAL_MAX_STREAMS_BIDI (既定 100) は受信値
    なので、それより小さいカプセルは Section 6.7 の MUST 違反になる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(server, session_id, _WT_MAX_STREAMS_BIDI, _encode_varint(1))
    _assert_flow_control_error_sent(server, "WT_MAX_STREAMS decreased")


def test_wt_max_streams_uni_decrease_closes_session() -> None:
    """前回受信値より小さい WT_MAX_STREAMS (uni) で WT_FLOW_CONTROL_ERROR になることを確認

    対向 SETTINGS の SETTINGS_WT_INITIAL_MAX_STREAMS_UNI (既定 100) は受信値
    なので、それより小さいカプセルは Section 6.7 の MUST 違反になる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(server, session_id, _WT_MAX_STREAMS_UNI, _encode_varint(1))
    _assert_flow_control_error_sent(server, "WT_MAX_STREAMS decreased")


def test_wt_max_streams_exceeds_2_60_closes_session() -> None:
    """2^60 を超える WT_MAX_STREAMS で WT_FLOW_CONTROL_ERROR になることを確認

    Section 6.7 の MUST 。 2^60 ちょうどは上限内なのでエラーにしない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(server, session_id, _WT_MAX_STREAMS_BIDI, _encode_varint(_MAX_STREAMS_LIMIT))
    _assert_no_flow_control_error_sent(server)

    _inject_capsule(server, session_id, _WT_MAX_STREAMS_UNI, _encode_varint(_MAX_STREAMS_LIMIT + 1))
    _assert_flow_control_error_sent(server, "WT_MAX_STREAMS exceeds 2^60")


def test_wt_streams_blocked_exceeds_2_60_closes_session() -> None:
    """2^60 を超える WT_STREAMS_BLOCKED で WT_FLOW_CONTROL_ERROR になることを確認

    Section 6.10 の MUST 。修正前はペイロード未解析のまま黙殺していた。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(
        server, session_id, _WT_STREAMS_BLOCKED_BIDI, _encode_varint(_MAX_STREAMS_LIMIT + 1)
    )
    _assert_flow_control_error_sent(server, "WT_STREAMS_BLOCKED exceeds 2^60")


def test_wt_streams_blocked_uni_exceeds_2_60_closes_session() -> None:
    """2^60 を超える WT_STREAMS_BLOCKED (uni) で WT_FLOW_CONTROL_ERROR になることを確認

    Section 6.10 は bidi / uni の両方に同じ 2^60 MUST を課す。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(
        server, session_id, _WT_STREAMS_BLOCKED_UNI, _encode_varint(_MAX_STREAMS_LIMIT + 1)
    )
    _assert_flow_control_error_sent(server, "WT_STREAMS_BLOCKED exceeds 2^60")


def test_wt_streams_blocked_decrease_does_not_close() -> None:
    """WT_STREAMS_BLOCKED の減少値はエラーにしないことを確認

    Section 6.10 に減少値の受信側 MUST は無く、 advisory な通知のため検証
    対象外。SETTINGS の 100 より小さい 50 を送ってもセッションは閉じない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(server, session_id, _WT_STREAMS_BLOCKED_BIDI, _encode_varint(50))
    _assert_no_flow_control_error_sent(server)


def test_wt_max_data_increase_and_equal_do_not_close() -> None:
    """WT_MAX_DATA の増加・同一値はエラーにしないことを確認

    受信値より大きい値はクレジット更新、同一値は冗長な通知として受け入れる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(server, session_id, _WT_MAX_DATA, _encode_varint(2_000_000))
    _assert_no_flow_control_error_sent(server)

    _inject_capsule(server, session_id, _WT_MAX_DATA, _encode_varint(2_000_000))
    _assert_no_flow_control_error_sent(server)


def test_wt_max_data_decrease_does_not_push_error_event() -> None:
    """減少値検知は Error イベントを push せず close_session のみで閉じることを確認

    受信フロー制御違反 (Error イベント push) とは経路を分け、本検知は
    close_session 直接呼び出しだけにする。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(server, session_id, _WT_MAX_DATA, _encode_varint(1))
    error_events = [event for event in _drain_events(server) if event.type == h2.EventType.ERROR]
    assert error_events == []
    _assert_flow_control_error_sent(server, "WT_MAX_DATA decreased")


def test_wt_max_data_first_capsule_no_decrease_does_not_close() -> None:
    """受信値 0 より大きい最初の WT_MAX_DATA を減少扱いしないことを確認

    クライアントが 0 の初期フロー制御 (wt_initial_max_data = 0) を広告した
    セッションでは、サーバーの「前回受信値」は未設定のまま (0 は記録されない)
    ため、最初のカプセルで 100 を受け取っても Section 6.5 の減少にはならない。
    受信後に 50 を受け取ると 100 からの減少になる。
    """
    client_config = h2.Config()
    client_config.wt_initial_max_data = 0
    client = h2.Session.create_client(client_config)
    server_config = h2.Config()
    server_config.is_server = True
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0, "CONNECT リクエストの送信に失敗しました"
    _h2_pump(client, server)

    ready_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1
    assert server.accept_session(session_id) is True, "セッションの受理に失敗しました"
    _h2_pump(server, client)
    # クライアントの初期 WT_MAX_DATA カプセル (値 0) はまだサーバーへ
    # 届いていない (SETTINGS の 0 は既に受信済みだが、クライアントの
    # セッション確立後の初期カプセルは _h2_pump(server, client) まで送られない)

    _inject_capsule(server, session_id, _WT_MAX_DATA, _encode_varint(100))
    _assert_no_flow_control_error_sent(server)

    _inject_capsule(server, session_id, _WT_MAX_DATA, _encode_varint(50))
    _assert_flow_control_error_sent(server, "WT_MAX_DATA decreased")
