"""WebTransport over HTTP/2 のストリーム状態検証テスト

不正な状態のストリームへの WT_STREAM / WT_RESET_STREAM capsule 受信と、
同一ストリームへの 2 回目の WT_STOP_SENDING 受信を検知して
WT_STREAM_STATE_ERROR を送出することを検証する。draft-15 の
MUST 違反の修正テストで、ピアからの不正カプセルはワイヤ注入で再現する
(公開 API では非コンプライアントなカプセルを送出する手段が存在しないため)。
エラー送出は close_session 経由の WT_CLOSE_SESSION (error code 0x51) で
実現され、ワイヤ部分列チェックで検証する。0x51 は WT_STREAM_STATE_ERROR
(0xTBD) のプレースホルダ (draft-15 Section 3.4)。
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


def _encode_wt_stream_capsule(stream_id: int, data: bytes, fin: bool = False) -> bytes:
    """WT_STREAM / WT_STREAM_FIN capsule のワイヤバイト列を組み立てる

    Type 0x190B4D3C (FIN なし) / 0x190B4D3B (FIN 付き) は 4 バイト varint
    + Length + Stream ID (varint) + Data。Length は 1 バイト varint のみ
    対応する (テストで使う小さい値のみ。64 バイト未満のペイロード前提)。
    """
    payload = _encode_varint(stream_id) + data
    assert len(payload) < 0x40, "Length が 1 バイト varint に収まる前提が崩れています"
    capsule_type = 0x190B4D3B if fin else 0x190B4D3C
    return bytes([0x99, 0x0B, 0x4D, capsule_type & 0xFF, len(payload)]) + payload


def _encode_wt_reset_stream_capsule(stream_id: int, error_code: int, reliable_size: int) -> bytes:
    """WT_RESET_STREAM capsule のワイヤバイト列を組み立てる

    Type 0x190B4D39 (4 バイト varint) + Length + Stream ID (varint) +
    Error Code (varint) + Reliable Size (varint)。Length は 1 バイト varint
    のみ対応する (テストで使う小さい値のみ。64 バイト未満のペイロード前提)。
    """
    payload = _encode_varint(stream_id) + _encode_varint(error_code) + _encode_varint(reliable_size)
    assert len(payload) < 0x40, "Length が 1 バイト varint に収まる前提が崩れています"
    return bytes([0x99, 0x0B, 0x4D, 0x39, len(payload)]) + payload


def _encode_wt_stop_sending_capsule(stream_id: int, error_code: int) -> bytes:
    """WT_STOP_SENDING capsule のワイヤバイト列を組み立てる

    Type 0x190B4D3A (4 バイト varint) + Length + Stream ID (varint) +
    Error Code (varint)。Length は 1 バイト varint のみ対応する (テストで
    使う小さい値のみ。64 バイト未満のペイロード前提)。
    """
    payload = _encode_varint(stream_id) + _encode_varint(error_code)
    assert len(payload) < 0x40, "Length が 1 バイト varint に収まる前提が崩れています"
    return bytes([0x99, 0x0B, 0x4D, 0x3A, len(payload)]) + payload


def _encode_data_frame(session_id: int, payload: bytes = b"") -> bytes:
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


def _encode_wt_close_session_capsule(error_code: int, error_message: str = "") -> bytes:
    """WT_CLOSE_SESSION capsule のワイヤバイト列を組み立てる

    Type 0x2843 は 2 バイト varint [0x68, 0x43] + Length + Application Error
    Code (32bit) + Message。Length は 1 バイト varint のみ対応する (テストで
    使う小さい値のみ。64 バイト未満のペイロード前提)。
    """
    payload = error_code.to_bytes(4, "big") + error_message.encode("utf-8")
    assert len(payload) < 0x40, "Length が 1 バイト varint に収まる前提が崩れています"
    return b"\x68\x43" + bytes([len(payload)]) + payload


# エラー検知時に close_session へ渡すメッセージ (テストのワイヤ検証と一致させる)
_WT_STREAM_TERMINAL = "WT_STREAM received for stream in terminal state"
_WT_RESET_TERMINAL = "WT_RESET_STREAM received for stream in terminal state"
_WT_RESET_MISMATCH = "WT_RESET_STREAM reliable size mismatch"
_WT_RESET_UNKNOWN = "WT_RESET_STREAM non-zero reliable size, unknown stream"
_WT_STOP_SENDING_DUPLICATE = "WT_STOP_SENDING received twice"


def _assert_state_error_sent(server: h2.Session, error_message: str) -> None:
    """WT_STREAM_STATE_ERROR (0x51) の WT_CLOSE_SESSION が送出されることを確認

    エラー検知は close_session (WT_CLOSE_SESSION 送出 + END_STREAM) で実現
    される (draft-15 Section 3.4 の「Prior to terminating a stream with an
    error, a WT_CLOSE_SESSION capsule with an application-specified error
    code MAY be sent」)。ワイヤ部分列チェックで送出を検証する。
    """
    wire = server.send()
    assert wire is not None
    assert _encode_wt_close_session_capsule(0x51, error_message) in wire


def _assert_no_state_error_sent(server: h2.Session) -> None:
    """WT_CLOSE_SESSION が送出されないことを確認

    0x68 0x43 は WT_CLOSE_SESSION (Type 0x2843) の 2 バイト varint。
    エラー検知 (close_session 呼び出し) があれば必ずワイヤに現れるため、
    Type の非存在でエラー送出なしを検証できる。
    """
    wire = server.send()
    assert wire is None or b"\x68\x43" not in wire


def test_wt_stream_after_reset_stream_sends_state_error() -> None:
    """リセット済み (ResetRecvd) ストリームへの WT_STREAM 受信で WT_STREAM_STATE_ERROR が送出されることを確認

    ピアの WT_RESET_STREAM 受信後はエントリが残存したまま recv_state が
    ResetRecvd になる。修正前は状態検証がなく、以後の WT_STREAM がそのまま
    処理されていた (draft-15 Section 6.4 の MUST 違反)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_RESET_STREAM (Reliable Size 0) を送信する
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(0, 42, 0)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    reset_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_RESET
    ]
    assert len(reset_events) == 1

    # リセット済みストリームへの WT_STREAM は stream error になる
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"x")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"
    _assert_state_error_sent(server, _WT_STREAM_TERMINAL)


def test_wt_stream_after_fin_sends_state_error() -> None:
    """FIN 受信済み (DataRecvd) ストリームへの WT_STREAM 受信で WT_STREAM_STATE_ERROR が送出されることを確認

    WT_STREAM_FIN 受信で recv_state が DataRecvd に遷移する (draft-15
    Section 5.2 の QUIC 状態ミラー)。以後の WT_STREAM は stream error に
    なる (Section 6.4 の MUST)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_STREAM_FIN を送信する
    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"fin", fin=True))
    )
    assert ret > 0, "WT_STREAM_FIN カプセルの注入に失敗しました"
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 1
    assert stream_events[0].fin is True

    # FIN 済みストリームへの WT_STREAM は stream error になる
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"x")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"
    _assert_state_error_sent(server, _WT_STREAM_TERMINAL)

    # 検知側 (サーバー) に Error イベント (0x51) が通知される
    # (受信フロー制御違反 0x50 と同じ方式)
    error_events = [event for event in _drain_events(server) if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == 0x51


def test_empty_wt_stream_fin_after_fin_ignored() -> None:
    """FIN 受信済み (DataRecvd) のストリームへの空の WT_STREAM_FIN が無視されることを確認

    空の WT_STREAM capsule は「ストリームを閉じる」操作として許容される
    (draft-15 Section 6.4 の「Empty WT_STREAM capsules MUST NOT be used
    unless they open or close a stream」)。実ブラウザ (WebKit) は FIN 送信後
    に空の WT_STREAM_FIN を送ることがあるため、終端状態への受信でもエラーに
    しない (データ付きの WT_STREAM は終端状態へのデータ送信を意味するため
    検知する)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_STREAM_FIN を送信する (DataRecvd に遷移させる)
    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"fin", fin=True))
    )
    assert ret > 0, "WT_STREAM_FIN カプセルの注入に失敗しました"

    # 終端状態への空の WT_STREAM_FIN は無視され、エラーは送出されない
    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"", fin=True))
    )
    assert ret > 0, "WT_STREAM_FIN カプセルの注入に失敗しました"
    _assert_no_state_error_sent(server)


def test_wt_reset_stream_after_fin_sends_state_error() -> None:
    """FIN 受信済み (DataRecvd) ストリームへの WT_RESET_STREAM 受信で WT_STREAM_STATE_ERROR が送出されることを確認

    WT_RESET_STREAM は終了済みストリームには送ってはならない (draft-15
    Section 6.2 の MUST)。受信側終端状態のストリームへの WT_RESET_STREAM は
    stream error になる。Reliable Size の一致・不一致は問わない (終端状態が
    先行して不正)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_STREAM_FIN を送信する (DataRecvd に遷移させる)
    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"fin", fin=True))
    )
    assert ret > 0, "WT_STREAM_FIN カプセルの注入に失敗しました"

    # 終端状態への WT_RESET_STREAM は stream error になる
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(0, 42, 3)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    _assert_state_error_sent(server, _WT_RESET_TERMINAL)

    # エラー検知時は StreamReset イベントを push しない
    assert all(event.type != h2.EventType.STREAM_RESET for event in _drain_events(server))


def test_wt_reset_stream_after_reset_stream_sends_state_error() -> None:
    """リセット済み (ResetRecvd) ストリームへの WT_RESET_STREAM 受信で WT_STREAM_STATE_ERROR が送出されることを確認

    WT_RESET_STREAM はリセット済みストリームには送ってはならない
    (draft-15 Section 6.2 の MUST)。受信側終端状態のストリームへの
    WT_RESET_STREAM は stream error になる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_RESET_STREAM (Reliable Size 0) を送信する (ResetRecvd に遷移させる)
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(0, 42, 0)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    reset_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_RESET
    ]
    assert len(reset_events) == 1

    # 終端状態への WT_RESET_STREAM は stream error になる
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(0, 42, 0)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    _assert_state_error_sent(server, _WT_RESET_TERMINAL)

    # エラー検知時は StreamReset イベントを push しない
    assert all(event.type != h2.EventType.STREAM_RESET for event in _drain_events(server))


def test_wt_reset_stream_reliable_size_mismatch_sends_state_error() -> None:
    """Reliable Size 不一致の WT_RESET_STREAM 受信で WT_STREAM_STATE_ERROR が送出されることを確認

    Reliable Size は受信済みバイト数と一致しなければならない (draft-15
    Section 6.2 の MUST)。不一致時は session error でセッションを閉じ、
    StreamReset イベントは push しない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_STREAM で 5 バイト送信する (bytes_received = 5)
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"01234")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"

    # Reliable Size 3 は受信済み 5 バイトと不一致 → session error
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(0, 42, 3)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    _assert_state_error_sent(server, _WT_RESET_MISMATCH)

    # エラー検知時は StreamReset イベントを push しない
    assert all(event.type != h2.EventType.STREAM_RESET for event in _drain_events(server))


def test_wt_reset_stream_unknown_stream_zero_accepted() -> None:
    """ストリーム不在の WT_RESET_STREAM (Reliable Size 0) が受け入れられることを確認

    真に未知のストリームは受信済みバイト数 0 として比較する (draft-15
    Section 6.2 の MUST)。Reliable Size = 0 なら受け入れ、エントリを作成して
    受信側を ResetRecvd へ遷移させる。以後の WT_STREAM は ResetRecvd として
    stream error になることで遷移を観測する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ストリーム不在の WT_RESET_STREAM (Reliable Size 0) を送信する
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(0, 42, 0)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"

    # エラーは送出されず、StreamReset イベントが発火する
    _assert_no_state_error_sent(server)
    reset_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_RESET
    ]
    assert len(reset_events) == 1
    assert reset_events[0].error_code == 42

    # 以後の WT_STREAM は ResetRecvd として stream error になる
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"x")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"
    _assert_state_error_sent(server, _WT_STREAM_TERMINAL)


def test_wt_reset_stream_unknown_stream_nonzero_sends_state_error() -> None:
    """ストリーム不在の WT_RESET_STREAM (Reliable Size > 0) で WT_STREAM_STATE_ERROR が送出されることを確認

    未知のストリームで Reliable Size > 0 は、届かないはずのデータを約束する
    ため session error でセッションを閉じる (draft-15 Section 6.2 の MUST)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ストリーム不在の WT_RESET_STREAM (Reliable Size 5) を送信する
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(0, 42, 5)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    _assert_state_error_sent(server, _WT_RESET_UNKNOWN)

    # エラー検知時は StreamReset イベントを push しない
    assert all(event.type != h2.EventType.STREAM_RESET for event in _drain_events(server))


def test_wt_reset_stream_reliable_size_match_delivered() -> None:
    """Reliable Size 一致の WT_RESET_STREAM が従来どおり StreamReset イベントになることを確認

    受信済みバイト数と一致する Reliable Size の WT_RESET_STREAM は正常系で、
    StreamReset イベントが発火しエラーは送出されない (回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_STREAM で 5 バイト送信する (bytes_received = 5)
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"01234")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"

    # Reliable Size 5 は受信済み 5 バイトと一致 → 正常に処理される
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(0, 42, 5)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"
    _assert_no_state_error_sent(server)
    reset_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_RESET
    ]
    assert len(reset_events) == 1
    assert reset_events[0].stream_id == 0
    assert reset_events[0].error_code == 42


def test_wt_stream_implicit_creation_delivered() -> None:
    """ストリーム不在の WT_STREAM が従来どおり暗黙作成されて StreamData イベントになることを確認

    WT_STREAM capsule はストリームを暗黙的に作成する (draft-15 Section 6.4)。
    最初の受信ではエラーにならない (回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_STREAM を送信する (暗黙作成)
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"hello")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"
    _assert_no_state_error_sent(server)
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 1
    assert stream_events[0].stream_id == 0
    assert stream_events[0].data == b"hello"


def test_wt_stream_after_local_reset_received() -> None:
    """自側 reset_stream 後のピアからの WT_STREAM が従来どおり受信されることを確認

    送信リセットは送信側の終了のみであり受信側は継続する (draft-15 Section
    5.2 の QUIC 状態ミラー)。自側 reset 後もエントリが保持されるため、ピア
    からの WT_STREAM はエラーにならず正常に StreamData イベントになる
    (回帰ピン。エントリを erase していた修正前は新規作成として再生成されて
    いた)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_STREAM を送信する (暗黙作成)
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"abc")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"

    # サーバーが自側 reset_stream を呼ぶ (送信側の終了)
    server.reset_stream(session_id, 0, 42)
    _h2_pump(server, client)

    # ピアからの WT_STREAM は受信側が継続するためエラーにならず受信される
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"more")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"
    _assert_no_state_error_sent(server)
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 2
    assert stream_events[1].data == b"more"


def test_send_stream_data_after_local_reset_not_sent() -> None:
    """リセット済みストリームへの send_stream_data が塞がれることを確認

    リセット後もエントリは保持されるため、send_state の確認で塞ぐ
    (draft-15 Section 6.4 の「A WT_STREAM capsule MUST NOT be sent after a
    stream is closed or reset」)。塞がないと WT_STREAM capsule が
    WT_RESET_STREAM の後ろに積まれてワイヤへ送出され得る。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # 送信してからリセットする
    client.send_stream_data(session_id, stream_id, b"before")
    client.reset_stream(session_id, stream_id, 42)
    _h2_pump(client, server)

    # リセット済みストリームへの send_stream_data はワイヤへ送出されない
    client.send_stream_data(session_id, stream_id, b"after-reset")
    wire = client.send()
    assert wire is None or _encode_wt_stream_capsule(stream_id, b"after-reset") not in wire


def test_after_state_error_following_capsules_in_same_receive_ignored() -> None:
    """エラー検知後の同一 receive() 内の後続カプセルが処理されないことを確認

    エラー検知 (close_session) 後は is_terminated が立ち、process_capsules
    のループ冒頭のチェックで同一 receive() 内の後続カプセルが遮断される。
    1 つの DATA フレームに [終端ストリームへの WT_STREAM, 別ストリームへの
    WT_STREAM] を連結して注入し、後者の StreamData イベントが発火しない
    ことを検証する (遮断がなければ発火する)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # まず終端状態 (DataRecvd) を作る
    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"fin", fin=True))
    )
    assert ret > 0, "WT_STREAM_FIN カプセルの注入に失敗しました"

    # 同一 receive() で [終端ストリームへの WT_STREAM, 別ストリームへの
    # WT_STREAM] を連結して注入する
    capsules = _encode_wt_stream_capsule(0, b"bad") + _encode_wt_stream_capsule(4, b"after")
    ret = server.receive(_encode_data_frame(session_id, capsules))
    assert ret > 0, "連結カプセルの注入に失敗しました"
    _assert_state_error_sent(server, _WT_STREAM_TERMINAL)

    # 1 つ目の不正 WT_STREAM (b"bad") はエラー検知で StreamData イベントを
    # push せず、後続の WT_STREAM (stream_id=4) の StreamData イベントも
    # 発火しない (遮断がなければどちらも push される)
    events = _drain_events(server)
    assert all(event.data != b"bad" for event in events)
    assert all(event.stream_id != 4 for event in events)


def test_peer_receives_session_closed_with_state_error() -> None:
    """エラー通知の WT_CLOSE_SESSION がピアに届いて SessionClosed (0x51) になることを確認

    エラー検知は close_session (WT_CLOSE_SESSION 送出 + END_STREAM) で実現
    される (draft-15 Section 3.4)。ピアは受信して SessionClosed イベント
    (error code 0x51) を得る。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピア (クライアント) が終端状態を作ってから不正な WT_STREAM を送る
    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"fin", fin=True))
    )
    assert ret > 0, "WT_STREAM_FIN カプセルの注入に失敗しました"
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"bad")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"

    # サーバーのエラー送出 (WT_CLOSE_SESSION + END_STREAM) をクライアントに届ける
    _h2_pump(server, client)

    closed_events = [
        event for event in _drain_events(client) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == session_id
    assert closed_events[0].error_code == 0x51


def test_wt_stream_after_matching_reset_sends_state_error() -> None:
    """Reliable Size 一致で ResetRecvd に遷移したストリームへの WT_STREAM で WT_STREAM_STATE_ERROR が送出されることを確認

    既存ストリームへの Reliable Size 一致の WT_RESET_STREAM は正常処理されて
    ResetRecvd に遷移し、以後の WT_STREAM は stream error になる
    (正常経路の遷移 → 終端検知の組を直接ピンする)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピアが WT_STREAM で 3 バイト送信する (bytes_received = 3)
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"abc")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"

    # Reliable Size 一致 (3) の WT_RESET_STREAM → ResetRecvd に遷移
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_reset_stream_capsule(0, 42, 3)))
    assert ret > 0, "WT_RESET_STREAM カプセルの注入に失敗しました"

    # 以後の WT_STREAM は stream error になる
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"x")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"
    _assert_state_error_sent(server, _WT_STREAM_TERMINAL)


def test_get_stream_ids_includes_reset_stream() -> None:
    """get_stream_ids にリセット済みストリームも含まれることを確認

    自側 reset_stream はエントリを保持する (draft-15 Section 5.2 の QUIC
    状態ミラー。送信リセットは受信側に影響しない) ため、get_stream_ids には
    リセット済みストリームも含まれる (修正前は erase されていたため
    含まれなかった)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # クライアントがリセットしてもエントリは保持される
    client.reset_stream(session_id, stream_id, 42)
    assert client.get_stream_ids(session_id) == [stream_id]


def _create_h2_session_pair_with_server_recv_limit(
    limit: int,
) -> tuple[h2.Session, h2.Session]:
    """サーバー側の受信上限 wt_initial_max_stream_data を縮小したペアを作成する

    フロー制御超過を発生させるテスト用。サーバー config の
    wt_initial_max_stream_data が受信側のストリーム上限 (暗黙作成時の
    max_stream_data_remote) になる。
    """
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_initial_max_stream_data = limit
    server = h2.Session.create_server(server_config)

    # クライアントの preface + SETTINGS をサーバーへ
    _h2_pump(client, server)
    # サーバーの SETTINGS をクライアントへ
    _h2_pump(server, client)

    return client, server


def test_wt_stream_flow_control_excess_on_terminal_stream_sends_state_error() -> None:
    """終端状態のストリームへのフロー制御超過データでも WT_STREAM_STATE_ERROR (0x51) が優先されることを確認

    状態検知はフロー制御チェックより前に置く (フロー制御違反の error code
    0x50 と区別するため)。終端状態のストリームに受信上限を超えるデータを
    注入しても、0x50 の Error イベントではなく 0x51 の WT_CLOSE_SESSION が
    送出される。順序が入れ替わると 0x50 の Error イベントが push されて
    0x51 は送出されないため、このテストが回帰を検出する。
    """
    client, server = _create_h2_session_pair_with_server_recv_limit(4)
    session_id = _connect_h2_session(client, server)

    # 終端状態 (DataRecvd) を作る
    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"fin", fin=True))
    )
    assert ret > 0, "WT_STREAM_FIN カプセルの注入に失敗しました"

    # 受信上限 4 バイトを超える WT_STREAM を注入しても 0x51 が優先される
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(0, b"12345")))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"
    _assert_state_error_sent(server, _WT_STREAM_TERMINAL)

    # 0x51 の Error イベントが push され、0x50 ではない (順序が入れ替わると
    # 0x50 の Error イベントが push されて 0x51 は送出されない)
    error_events = [event for event in _drain_events(server) if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == 0x51


def test_wt_stop_sending_first_delivers_event() -> None:
    """1 回目の WT_STOP_SENDING 受信で StopSending イベントが届くことを確認

    Section 6.3 の MUST は 2 回目にだけ WT_STREAM_STATE_ERROR を要求する。
    1 回目は従来どおりイベントを push し、セッションは閉じない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stop_sending_capsule(0, 42)))
    assert ret > 0, "WT_STOP_SENDING カプセルの注入に失敗しました"
    stop_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STOP_SENDING
    ]
    assert len(stop_events) == 1
    assert stop_events[0].session_id == session_id
    assert stop_events[0].stream_id == 0
    assert stop_events[0].error_code == 42
    _assert_no_state_error_sent(server)


def test_wt_stop_sending_second_sends_state_error() -> None:
    """同一ストリームへの 2 回目の WT_STOP_SENDING で WT_STREAM_STATE_ERROR になることを確認

    HTTP/2 は順序保証があるため冗長な STOP_SENDING は不要で、2 回目は
    Section 6.3 の MUST 違反になる。修正前は毎回 StopSending を push する
    だけで検知しなかった。2 回目はイベントを push せずセッションを閉じる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stop_sending_capsule(0, 42)))
    assert ret > 0, "1 回目の WT_STOP_SENDING カプセルの注入に失敗しました"
    stop_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STOP_SENDING
    ]
    assert len(stop_events) == 1

    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stop_sending_capsule(0, 43)))
    assert ret > 0, "2 回目の WT_STOP_SENDING カプセルの注入に失敗しました"
    events = _drain_events(server)
    stop_events = [event for event in events if event.type == h2.EventType.STOP_SENDING]
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert stop_events == []
    assert len(error_events) == 1
    assert error_events[0].error_code == 0x51
    assert error_events[0].stream_id == 0
    _assert_state_error_sent(server, _WT_STOP_SENDING_DUPLICATE)


def test_wt_stop_sending_duplicate_in_same_receive_sends_state_error() -> None:
    """同一 receive() 内の 2 個目の WT_STOP_SENDING でも WT_STREAM_STATE_ERROR になることを確認

    HTTP/2 の順序保証では同一 DATA に隣接カプセルが載る。1 回目は
    StopSending を push し、2 回目でセッションを閉じる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    capsules = _encode_wt_stop_sending_capsule(0, 1) + _encode_wt_stop_sending_capsule(0, 2)
    ret = server.receive(_encode_data_frame(session_id, capsules))
    assert ret > 0, "連結した WT_STOP_SENDING カプセルの注入に失敗しました"
    events = _drain_events(server)
    stop_events = [event for event in events if event.type == h2.EventType.STOP_SENDING]
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(stop_events) == 1
    assert stop_events[0].error_code == 1
    assert len(error_events) == 1
    assert error_events[0].error_code == 0x51
    assert error_events[0].stream_id == 0
    _assert_state_error_sent(server, _WT_STOP_SENDING_DUPLICATE)


def test_wt_stop_sending_unknown_stream_second_sends_state_error() -> None:
    """未作成ストリームへの 2 回目の WT_STOP_SENDING でも WT_STREAM_STATE_ERROR になることを確認

    仕様違反ピアは未知の Stream ID 宛に送れる。WtStreamInfo のフラグでは
    エントリが無い ID の 2 回目を検出できないため、セッション単位の集合で
    追跡する。暗黙のストリーム作成は get_stream_ids に偽のストリームを
    露出させるため行わない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    unknown_stream_id = 99

    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stop_sending_capsule(unknown_stream_id, 1))
    )
    assert ret > 0, "1 回目の WT_STOP_SENDING カプセルの注入に失敗しました"
    assert server.get_stream_ids(session_id) == []
    stop_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STOP_SENDING
    ]
    assert len(stop_events) == 1
    assert stop_events[0].stream_id == unknown_stream_id

    ret = server.receive(
        _encode_data_frame(session_id, _encode_wt_stop_sending_capsule(unknown_stream_id, 1))
    )
    assert ret > 0, "2 回目の WT_STOP_SENDING カプセルの注入に失敗しました"
    assert server.get_stream_ids(session_id) == []
    events = _drain_events(server)
    stop_events = [event for event in events if event.type == h2.EventType.STOP_SENDING]
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert stop_events == []
    assert len(error_events) == 1
    assert error_events[0].error_code == 0x51
    assert error_events[0].stream_id == unknown_stream_id
    _assert_state_error_sent(server, _WT_STOP_SENDING_DUPLICATE)


def test_wt_stop_sending_different_streams_each_deliver_once() -> None:
    """異なるストリームへの 1 回目の WT_STOP_SENDING はどちらもイベントになることを確認

    二重受信の判定は Stream ID ごとであり、別ストリームの 1 回目を 2 回目と
    誤検出しない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stop_sending_capsule(0, 1)))
    assert ret > 0, "ストリーム 0 の WT_STOP_SENDING カプセルの注入に失敗しました"
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stop_sending_capsule(4, 2)))
    assert ret > 0, "ストリーム 4 の WT_STOP_SENDING カプセルの注入に失敗しました"

    stop_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STOP_SENDING
    ]
    assert [(event.stream_id, event.error_code) for event in stop_events] == [(0, 1), (4, 2)]
    _assert_no_state_error_sent(server)
