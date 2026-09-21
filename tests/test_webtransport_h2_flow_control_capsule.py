"""WebTransport over HTTP/2 のフロー制御カプセル受信値検証テスト

draft-15 Section 6.5 / 6.6 / 6.7 の MUST 「前回受信値より小さい
WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS は
WT_FLOW_CONTROL_ERROR」と、 Section 6.7 / 6.10 の MUST 「Maximum Streams が
2^60 を超える値は WT_FLOW_CONTROL_ERROR」を検証する。あわせて Section 6.9 の
WT_STREAM_DATA_BLOCKED が、有効な状態のストリームと未使用の Stream ID では
受理されセッションが維持されることも検証する。不正カプセルは
ワイヤ注入で再現する (公開 API では非コンプライアントな値を送出する
手段が存在しないため)。セッション閉鎖は close_session 経由の
WT_CLOSE_SESSION (error code 0x50) で実現され、ワイヤ部分列チェックで
検証する。WT_FLOW_CONTROL_ERROR (0xTBD) のプレースホルダ
(draft-15 Section 3.4)。

Section 6.8 の WT_DATA_BLOCKED も対象に含む。Maximum Data (可変長整数) を
受理し、advisory な通知として扱って自側のフロー制御状態とクレジットを
更新しないこと、アプリへイベントを push しないことを検証する。ペイロードの
形 (フィールドの不足・不完全な可変長整数・余分なバイト) の検証は、カプセル
横断の tests/test_webtransport_h2_incomplete_capsule_payload.py と
tests/test_webtransport_h2_capsule_trailing_bytes.py が担う。
"""

from __future__ import annotations

import pytest
from conftest import (
    _connect_h2_session,
    _create_h2_session_pair,
    _drain_events,
    _encode_capsule,
    _encode_data_frame,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2
from webtransport.webtransport_ext.h2 import WtErrorCode

# エラーコードは WtErrorCode を単一の出典とする (draft-15 Section 3.4 の
# 0x50 / 0x51 / 0x52 は 0xTBD のプレースホルダ)
WT_FLOW_CONTROL_ERROR = WtErrorCode.WT_FLOW_CONTROL_ERROR.value
WT_STREAM_STATE_ERROR = WtErrorCode.WT_STREAM_STATE_ERROR.value
WT_ERROR = WtErrorCode.WT_ERROR.value


# Capsule Type (draft-15 Section 6)
_WT_MAX_DATA = 0x190B4D3D
_WT_MAX_STREAM_DATA = 0x190B4D3E
_WT_MAX_STREAMS_BIDI = 0x190B4D3F
_WT_MAX_STREAMS_UNI = 0x190B4D40
_WT_DATA_BLOCKED = 0x190B4D41
_WT_STREAM_DATA_BLOCKED = 0x190B4D42
_WT_STREAM_FIN = 0x190B4D3B
_WT_STREAMS_BLOCKED_BIDI = 0x190B4D43
_WT_STREAMS_BLOCKED_UNI = 0x190B4D44

# Maximum Streams の上限は 2^60
_MAX_STREAMS_LIMIT = 1 << 60

# WT_DATA_BLOCKED の Maximum Data に使う可変長整数の値とワイヤ長。1 バイトは
# 最小値 0、2 / 4 / 8 バイトは各符号化長で表せる最大値 (それぞれ 2^14 - 1、
# 2^30 - 1、可変長整数の上限 2^62 - 1。RFC 9000 Section 16)
_MAX_VARINT_VALUE = (1 << 62) - 1
_DATA_BLOCKED_VARINTS = [
    (0, 1),
    ((1 << 14) - 1, 2),
    ((1 << 30) - 1, 4),
    (_MAX_VARINT_VALUE, 8),
]


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

    WT_FLOW_CONTROL_ERROR は draft-15 Section 3.4 の 0xTBD のプレースホルダ。draft で値が
    確定したら更新する。
    """
    wire = server.send()
    assert wire is not None
    assert _encode_wt_close_session_capsule(WT_FLOW_CONTROL_ERROR, error_message) in wire


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


@pytest.mark.parametrize(
    ("value", "wire_length"),
    _DATA_BLOCKED_VARINTS,
    ids=["1byte", "2byte", "4byte", "8byte"],
)
def test_wt_data_blocked_accepted_for_valid_varint_lengths(value: int, wire_length: int) -> None:
    """Maximum Data が正しい WT_DATA_BLOCKED が受理されセッションが存続することを確認

    draft-15 Section 6.8 は Maximum Data を可変長整数と定めるため、1 / 2 / 4 /
    8 バイトのいずれの符号化も受け入れる。値域の MUST は無いため上限
    (2^62 - 1) も受理する。フィールドの読み出しを実装していないと、この
    対照テストは (修正前でも) 通ってしまうため RED の観測対象ではない。
    """
    assert len(_encode_varint(value)) == wire_length, "前提: 可変長整数のワイヤ長"

    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(server, session_id, _WT_DATA_BLOCKED, _encode_varint(value))

    _assert_no_flow_control_error_sent(server)
    assert not [event for event in _drain_events(server) if event.type == h2.EventType.ERROR], (
        "Error イベントが発火しました"
    )
    assert server.get_session_ids() == [session_id], "セッションが終了しています"


def test_wt_data_blocked_accepted_for_non_minimal_varint() -> None:
    """値 0 を 8 バイトで符号化した Maximum Data も受理されることを確認

    RFC 9297 Section 1.1 と RFC 9000 Section 16 は「値は必要最小限のバイト数で
    符号化する必要はない」とするため、非最小符号化も受理しなければならない。
    0xc0 は 8 バイト可変長整数のプレフィックスであり、`decode_varint` は値では
    なくプレフィックスから消費バイト数を決めるため、この入力は
    フィールド 1 つでペイロードが終端する (長さの一致検証それ自体の判別は、
    余分なバイトを持つ入力を使うカプセル横断の
    tests/test_webtransport_h2_capsule_trailing_bytes.py が担う)。

    受理だけを表明するテストであり、ペイロードを検証しない修正前実装
    (no-op 分岐) でも通る。RED の観測対象ではない。
    """
    payload = b"\xc0" + b"\x00" * 7

    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_capsule(server, session_id, _WT_DATA_BLOCKED, payload)

    _assert_no_flow_control_error_sent(server)
    assert not [event for event in _drain_events(server) if event.type == h2.EventType.ERROR], (
        "Error イベントが発火しました"
    )
    assert server.get_session_ids() == [session_id], "セッションが終了しています"


def test_wt_data_blocked_does_not_update_flow_control_state() -> None:
    """受理した WT_DATA_BLOCKED が自側のフロー制御状態を更新しないことを確認

    draft-15 Section 6.8 の Maximum Data は「ブロックが発生したセッション
    レベルの上限」の申告であり、WT_MAX_DATA と違って自側の送信上限
    (max_data_local) を書き換えない。送信クレジットを観測点にして、既定値より
    大きい値を送っても残量が変わらないこと (状態更新が起きていないこと) を
    固定する。イベントも push しない (WT_STREAMS_BLOCKED と同じ advisory な
    通知)。

    続けて WT_MAX_DATA を注入し、WT_DATA_BLOCKED が前回受信値
    (received_max_data) を汚染していないことも固定する。汚染していれば
    1_500_000 は「減少」と誤判定され WT_FLOW_CONTROL_ERROR になる。

    カプセルを無視する修正前実装 (no-op 分岐) でも通る。RED の観測対象では
    ない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    credit_before = server.get_send_credit(session_id)
    assert credit_before == h2.Config().wt_initial_max_data, (
        "前提: 初期クレジットが対向の広告値と一致していません"
    )

    _inject_capsule(server, session_id, _WT_DATA_BLOCKED, _encode_varint(2_000_000))

    assert server.get_send_credit(session_id) == credit_before, (
        "WT_DATA_BLOCKED が送信クレジットを更新しています"
    )
    assert _drain_events(server) == [], "WT_DATA_BLOCKED でイベントが発火しました"
    assert server.get_session_ids() == [session_id], "セッションが終了しています"

    # 前回受信値が WT_DATA_BLOCKED の値 (2_000_000) に汚染されていなければ、
    # SETTINGS 由来の 1_048_576 からの増加として受理される
    _inject_capsule(server, session_id, _WT_MAX_DATA, _encode_varint(1_500_000))
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


def test_get_send_credit_observes_remaining_credit() -> None:
    """get_send_credit がセッションレベルの送信残量を返すことを確認 (観測専用 API)

    初期値は対向 (サーバー) の wt_initial_max_data と一致し、送信するたびに
    減る。枯渇後は 0 になる。存在しないセッション ID では 0 を返す。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    initial_credit = client.get_send_credit(session_id)
    assert initial_credit == h2.Config().wt_initial_max_data

    # 送信すると残量が減る
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    body = b"x" * 100
    client.send_stream_data(session_id, stream_id, body, fin=True)
    _h2_pump(client, server)

    assert client.get_send_credit(session_id) == initial_credit - len(body)

    # 存在しないセッション ID では 0
    assert client.get_send_credit(session_id + 2) == 0


def test_get_send_credit_reaches_zero_when_exhausted() -> None:
    """セッションクレジットが枯渇すると get_send_credit が 0 になることを確認

    既定の 1 MiB ではストリームクレジット (262144) が先に尽きて保留キューに
    積まれるため、セッションクレジットを小さくした設定で枯渇させる。
    """
    client_config = h2.Config()
    client_config.wt_initial_max_data = 4096
    client_config.wt_initial_max_stream_data = 8192
    server_config = h2.Config()
    server_config.is_server = True
    # クライアントから見た送信クレジットは対向 (サーバー) の広告値で決まる
    server_config.wt_initial_max_data = 4096
    server_config.wt_initial_max_stream_data = 8192
    client = h2.Session.create_client(client_config)
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    session_id = _connect_h2_session(client, server)

    assert client.get_send_credit(session_id) == 4096

    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0
    # セッションクレジットを使い切る (ストリームクレジットは十分にある)
    client.send_stream_data(session_id, stream_id, b"y" * 4096)
    _h2_pump(client, server)

    assert client.get_send_credit(session_id) == 0


def test_wt_stream_data_blocked_accepted_for_open_stream() -> None:
    """recv_state が終端でないストリームへの WT_STREAM_DATA_BLOCKED が受理されることを確認

    draft-15 Section 6.9 の MUST は「not in a valid state」への受信だけを
    対象とする。開いたままのストリームへの申告は受理し、セッションを閉じない
    (ピアの申告であり自側のフロー制御状態も変えない)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    _h2_pump(client, server)
    _h2_pump(server, client)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # ピアがデータを送ってサーバー側にストリームエントリを作る
    client.send_stream_data(session_id, stream_id, b"ab", False)
    _h2_pump(client, server)
    _drain_events(server)

    _inject_capsule(
        server,
        session_id,
        _WT_STREAM_DATA_BLOCKED,
        _encode_varint(stream_id) + _encode_varint(1024),
    )

    _assert_no_flow_control_error_sent(server)
    assert not [event for event in _drain_events(server) if event.type == h2.EventType.ERROR]
    assert server.get_session_ids() == [session_id]
    assert server.get_stream_ids(session_id) == [stream_id]


def test_wt_stream_data_blocked_accepted_for_unknown_stream() -> None:
    """未使用の Stream ID への WT_STREAM_DATA_BLOCKED が受理されエントリを作らないことを確認

    エントリが無い場合は検証対象の状態が無いため受理する。ストリームを
    暗黙作成しないこと (get_stream_ids が変化しないこと) も確認する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    _h2_pump(client, server)
    _h2_pump(server, client)

    _inject_capsule(
        server,
        session_id,
        _WT_STREAM_DATA_BLOCKED,
        _encode_varint(0) + _encode_varint(1024),
    )

    _assert_no_flow_control_error_sent(server)
    assert not [event for event in _drain_events(server) if event.type == h2.EventType.ERROR]
    assert server.get_session_ids() == [session_id]
    assert server.get_stream_ids(session_id) == []


def test_wt_stream_data_blocked_accepted_after_local_fin() -> None:
    """自側が FIN を送出しただけのストリームへの WT_STREAM_DATA_BLOCKED が受理されることを確認

    自側の FIN は自側の送信方向だけを閉じるため、ピアの送信方向 (自側の
    受信方向) は継続する。状態検証に send_state を含めず recv_state の終端
    だけを見ることの回帰ピン (send_state を条件に加えると誤って拒否する)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    _h2_pump(client, server)
    _h2_pump(server, client)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # ピアがデータを送ってサーバー側にストリームエントリを作る
    client.send_stream_data(session_id, stream_id, b"ab", False)
    _h2_pump(client, server)
    _drain_events(server)

    # 自側 (サーバー) が FIN を送出して送信側だけを終端する
    # (send_state が実際に DataSent になったことをワイヤで確認する。
    # クレジット不足で保留になるとこのテストの前提が崩れるため)
    server.send_stream_data(session_id, stream_id, b"xy", True)
    wire = server.send()
    assert wire is not None, "自側の FIN が送出されていません"
    assert _encode_capsule(_WT_STREAM_FIN, _encode_varint(stream_id) + b"xy") in wire
    client.receive(wire)
    _drain_events(client)

    _inject_capsule(
        server,
        session_id,
        _WT_STREAM_DATA_BLOCKED,
        _encode_varint(stream_id) + _encode_varint(1024),
    )

    _assert_no_flow_control_error_sent(server)
    assert not [event for event in _drain_events(server) if event.type == h2.EventType.ERROR]
    assert server.get_session_ids() == [session_id]
    assert server.get_stream_ids(session_id) == [stream_id]
