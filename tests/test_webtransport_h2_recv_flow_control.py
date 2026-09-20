"""WebTransport over HTTP/2 の受信フロー制御違反と受信ウィンドウ返却のテスト

draft-15 Section 6.5 / 6.6 の MUST 「受信データが広告した WT_MAX_DATA /
WT_MAX_STREAM_DATA を超えたら WT_FLOW_CONTROL_ERROR でセッションを閉じる」
を検証する。不正な超過データはワイヤ注入で再現する (公開 API の
send_stream_data は送信側クレジットで塞がれ、超過分を送れないため)。
セッション閉鎖は close_session 経由の WT_CLOSE_SESSION (error code 0x50)
で実現され、あわせて Error イベント (WT_FLOW_CONTROL_ERROR) を push する。WT_FLOW_CONTROL_ERROR (0x50) は 0xTBD のプレースホルダ (draft-15 Section 3.4)。

あわせて、アプリへ配送せず破棄した受信バイトの後始末 (ストリーム終了経路での
未消費受信バイト記録の解放と、コネクションレベル受信ウィンドウの返却) を
検証する。返却しないと漏れの合計が 32768 バイトを超えた時点で (残りの受信
ウィンドウが閾値 32767 に届かず) WINDOW_UPDATE を送出できなくなり、接続が
恒久的に停止する。
"""

from __future__ import annotations

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


_WT_STREAM = 0x190B4D3C
_PEER_EXCEEDED = "peer exceeded flow control limit"

# nghttp2 がコネクションレベルの WINDOW_UPDATE を積むのは、返却量が接続の
# 受信ウィンドウの半分以上のときだけである (nghttp2 の
# nghttp2_should_send_window_update は local_window_size / 2 と比較する)。
# 接続の受信ウィンドウは RFC 9113 Section 6.9.2 の既定値 65535 で、
# SETTINGS_INITIAL_WINDOW_SIZE の対象はストリーム単位であり接続ウィンドウは
# WINDOW_UPDATE でしか変更できない (nghttp2 も
# NGHTTP2_INITIAL_CONNECTION_WINDOW_SIZE 固定)。未返却分がこの値未満であれば、
# 以後の消費と合算されて WINDOW_UPDATE が送出される (恒久停止しない)
_WINDOW_UPDATE_THRESHOLD = 65535 // 2


def _encode_wt_stream_capsule(stream_id: int, data: bytes) -> bytes:
    """WT_STREAM capsule (FIN なし) のワイヤバイト列を組み立てる"""
    return _encode_capsule(_WT_STREAM, _encode_varint(stream_id) + data)


def _encode_wt_close_session_capsule(error_code: int, error_message: str) -> bytes:
    """WT_CLOSE_SESSION capsule のワイヤバイト列を組み立てる"""
    payload = error_code.to_bytes(4, "big") + error_message.encode("utf-8")
    return _encode_capsule(0x2843, payload)


def _assert_flow_control_error_sent(server: h2.Session) -> None:
    """WT_FLOW_CONTROL_ERROR (0x50) の WT_CLOSE_SESSION が送出されることを確認

    WT_FLOW_CONTROL_ERROR は draft-15 Section 3.4 の 0xTBD のプレースホルダ。draft で値が
    確定したら更新する。
    """
    wire = server.send()
    assert wire is not None
    assert _encode_wt_close_session_capsule(WT_FLOW_CONTROL_ERROR, _PEER_EXCEEDED) in wire


def _assert_no_close_session_sent(server: h2.Session) -> None:
    """WT_CLOSE_SESSION が送出されないことを確認"""
    wire = server.send()
    assert wire is None or b"\x68\x43" not in wire


def _inject_wt_stream(server: h2.Session, session_id: int, stream_id: int, data: bytes) -> None:
    """サーバーへ WT_STREAM カプセルを DATA フレームとして注入する"""
    ret = server.receive(_encode_data_frame(session_id, _encode_wt_stream_capsule(stream_id, data)))
    assert ret > 0, "WT_STREAM カプセルの注入に失敗しました"


def _create_server_with_recv_limits(
    max_data: int, max_stream_data: int
) -> tuple[h2.Session, h2.Session]:
    """サーバーの受信上限を指定したセッションペアを作成する

    wt_initial_max_data がセッション受信上限 (max_data_remote)、
    wt_initial_max_stream_data がストリーム受信上限 (max_stream_data_remote)
    になる。
    """
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.wt_initial_max_data = max_data
    server_config.wt_initial_max_stream_data = max_stream_data
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)
    return client, server


def _has_connection_window_update(wire: bytes) -> bool:
    """WINDOW_UPDATE (Type 0x08, Stream ID 0) が含まれるか確認する

    フレームは Length (3 バイト) + Type + Flags + Stream ID (4 バイト) +
    Window Size Increment (4 バイト) で、コネクションレベルは Stream ID が 0
    である。フレーム境界をたどって判定する (DATA のペイロード中に同じ
    バイト列が現れても誤検知しない)。
    """
    offset = 0
    while offset + 9 <= len(wire):
        length = int.from_bytes(wire[offset : offset + 3], "big")
        frame_type = wire[offset + 3]
        stream_id = int.from_bytes(wire[offset + 5 : offset + 9], "big") & 0x7FFFFFFF
        if frame_type == 0x08 and stream_id == 0:
            return True
        offset += 9 + length
    return False


def _encode_rst_stream_frame(stream_id: int, error_code: int = 0) -> bytes:
    """RST_STREAM フレーム (Type 0x03) のワイヤバイト列を組み立てる

    h2 の公開 API に HTTP/2 ストリーム自体のリセットを送出する手段が無い
    ため、ワイヤ注入で再現する (RFC 9113 Section 6.4)。既定の error_code 0 は
    同節の NO_ERROR。
    """
    payload = error_code.to_bytes(4, "big")
    return (
        len(payload).to_bytes(3, "big")
        + bytes([0x03, 0x00])
        + (stream_id & 0x7FFFFFFF).to_bytes(4, "big")
        + payload
    )


def _inject_incomplete_capsule(server: h2.Session, session_id: int) -> None:
    """未完成カプセル (宣言長 16 に対してペイロード 4 バイト) を DATA で注入する

    カプセルのヘッダー 2 バイトと未完成のペイロード 4 バイトの計 6 バイトが
    未消費の受信バイトとして残る (完成したカプセルは process_capsule が
    ワイヤ長を消費するため残らない)。テストの前提として、記録がちょうど
    この 6 バイトになっていることを表明する。
    """
    incomplete = _encode_varint(0x00) + _encode_varint(16) + b"abcd"
    ret = server.receive(_encode_data_frame(session_id, incomplete))
    assert ret > 0, "未完成カプセルの注入に失敗しました"
    assert server._test_unconsumed_recv_bytes(session_id) == 6, (
        "未完成カプセルが未消費の記録に残っていない (6 バイト)"
    )


def test_wt_stream_exceeds_max_stream_data_closes_session() -> None:
    """WT_MAX_STREAM_DATA 超過で WT_FLOW_CONTROL_ERROR になることを確認

    ストリーム受信上限 4 バイトに対して 5 バイトを注入する。修正前は Error
    イベントを push するだけでセッションは閉じず、超過データは捨てられて
    ピアは送り続けられた。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"12345")
    _assert_flow_control_error_sent(server)


def test_wt_stream_exceeds_max_data_closes_session() -> None:
    """WT_MAX_DATA 超過で WT_FLOW_CONTROL_ERROR になることを確認

    セッション受信上限 4 バイト・ストリーム上限 100 バイトに対して 5 バイト
    を注入し、セッション上限側の超過経路を検証する。
    """
    client, server = _create_server_with_recv_limits(max_data=4, max_stream_data=100)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"12345")
    _assert_flow_control_error_sent(server)


def test_wt_stream_exceeds_flow_control_pushes_error_event() -> None:
    """受信超過は Error イベント (WT_FLOW_CONTROL_ERROR) を push したうえでセッションを閉じることを確認

    カプセル値減少の検知 (Error を push しない) とは経路を分け、受信超過は
    高レベル層の on_error 通知のために Error イベントを残す。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"12345")
    events = _drain_events(server)
    error_events = [event for event in events if event.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    assert error_events[0].error_code == WT_FLOW_CONTROL_ERROR
    assert error_events[0].error_message == _PEER_EXCEEDED
    assert error_events[0].session_id == session_id
    assert error_events[0].stream_id == 0
    stream_events = [event for event in events if event.type == h2.EventType.STREAM_DATA]
    assert stream_events == []
    _assert_flow_control_error_sent(server)


def test_wt_stream_cumulative_exceeds_max_stream_data_closes_session() -> None:
    """複数 WT_STREAM の累積がストリーム受信上限を超えたら閉じることを確認

    上限 4 バイトに対して 3 バイトのあと 5 バイトを送り、2 回目で超過する。
    1 回目の受信で補充 (3 + 4 = 7) が送出されるため、2 回目は 7 を超える
    量で超過させる。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"123")
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 1
    assert stream_events[0].data == b"123"
    _assert_no_close_session_sent(server)

    _inject_wt_stream(server, session_id, 0, b"45678")
    _assert_flow_control_error_sent(server)


def test_wt_stream_cumulative_exceeds_max_data_closes_session() -> None:
    """複数 WT_STREAM の累積がセッション受信上限を超えたら閉じることを確認

    セッション上限 4 バイト・ストリーム上限 100 バイトに対して 3 バイトの
    あと 5 バイトを送り、2 回目でセッション上限を超える。1 回目の受信で
    補充 (3 + 4 = 7) が送出されるため、2 回目は 7 を超える量で超過させる。
    """
    client, server = _create_server_with_recv_limits(max_data=4, max_stream_data=100)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"123")
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 1
    assert stream_events[0].data == b"123"
    _assert_no_close_session_sent(server)

    _inject_wt_stream(server, session_id, 0, b"45678")
    _assert_flow_control_error_sent(server)


def test_wt_stream_within_recv_limit_does_not_close() -> None:
    """受信上限ちょうどの WT_STREAM はセッションを閉じないことを確認

    上限 4 バイトに対して 4 バイトは超過ではない。StreamData が届き、
    WT_CLOSE_SESSION は送出されない。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"1234")
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 1
    assert stream_events[0].data == b"1234"
    _assert_no_close_session_sent(server)


def test_wt_stream_exceeds_flow_control_ignores_following_capsules() -> None:
    """受信超過の検知後、同一 receive() 内の後続カプセルが処理されないことを確認

    close_session が is_terminated を立て、process_capsules が後続を捨てる。
    超過の直後に別ストリームの WT_STREAM を連結しても StreamData は届かない。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    capsules = _encode_wt_stream_capsule(0, b"12345") + _encode_wt_stream_capsule(4, b"x")
    ret = server.receive(_encode_data_frame(session_id, capsules))
    assert ret > 0, "連結カプセルの注入に失敗しました"
    events = _drain_events(server)
    assert all(event.stream_id != 4 for event in events)
    assert all(event.type != h2.EventType.STREAM_DATA for event in events)
    _assert_flow_control_error_sent(server)


def test_peer_cannot_continue_sending_after_recv_flow_control_error() -> None:
    """受信超過で閉じたあと、後続の receive() では超過データが届かないことを確認

    修正前はセッションが開いたままだったため、次の WT_STREAM も受信処理に
    入った (超過分は捨てられるだけだった)。閉じたあとは is_established が
    落ち、新規 DATA は process_capsules に渡らない。
    """
    client, server = _create_server_with_recv_limits(max_data=1_048_576, max_stream_data=4)
    session_id = _connect_h2_session(client, server)

    _inject_wt_stream(server, session_id, 0, b"12345")
    _assert_flow_control_error_sent(server)

    _inject_wt_stream(server, session_id, 0, b"y")
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert stream_events == []


def test_h2_receive_backpressure_bounds_unconsumed_bytes() -> None:
    """アプリが消費しない間、未完成カプセルの保持バイト数が有界であることを確認

    自動 WINDOW_UPDATE を無効化し、アプリがイベントを消費した分だけ
    ウィンドウを開く。アプリが読まない状態で大量に送っても、受信側が保持する
    未完成バイト数は上限 (1 フレーム強) に留まる。イベントを読まずに観測
    できる `_test_unfinished_capsule_bytes` で白箱確認する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # コネクション初期ウィンドウ (65535) を大きく超える 256 KiB を積む
    chunk = b"x" * 32768
    for _ in range(8):
        client.send_stream_data(session_id, stream_id, chunk, False)

    peak = 0
    delivered = 0
    for _ in range(30):
        _h2_pump(client, server)
        _h2_pump(server, client)
        current = server._test_unfinished_capsule_bytes(session_id)
        assert current is not None
        peak = max(peak, current)
        delivered += sum(
            len(event.data)
            for event in _drain_events(server)
            if event.type == h2.EventType.STREAM_DATA
        )

    # アプリは結局すべて消費するため全量が届く
    assert delivered == len(chunk) * 8
    # 保持バイト数は 1 フレーム強に有界 (256 KiB を丸ごと保持しない)
    assert peak <= 65536, f"未完成カプセルの保持が有界でない: {peak}"


def test_unfinished_capsule_larger_than_window_does_not_deadlock() -> None:
    """受信ウィンドウより大きいカプセルでもデッドロックしないことを確認

    完成したカプセルだけを消費すると、1 つのカプセルが受信ウィンドウより
    大きい場合にウィンドウが戻らず永久に完成しない。未完成バイトの超過分を
    消費してウィンドウを開けることで最後まで受信できる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # コネクション初期ウィンドウ (65535) より大きい 256 KiB を送る
    payload = b"z" * (256 * 1024)
    client.send_stream_data(session_id, stream_id, payload, False)

    received = bytearray()
    for _ in range(200):
        _h2_pump(client, server)
        for event in _drain_events(server):
            if event.type == h2.EventType.STREAM_DATA:
                received.extend(event.data)
        _h2_pump(server, client)
        if len(received) == len(payload):
            break

    assert bytes(received) == payload


def test_pre_accept_413_releases_unconsumed_recv_bytes_and_window() -> None:
    """413 拒否で未消費受信バイトの記録が解放され、コネクションレベル受信ウィンドウが戻ることを確認

    受理前の楽観的カプセルが蓄積上限を超えると 413 で拒否され、wt_sessions_
    のエントリが削除されて以後の consume_recv_bytes の経路が塞がる。拒否で
    破棄したバイトをコネクションレベル受信ウィンドウへ返さないと消費した
    ままになり、漏れの合計が 32768 バイトを超える (残りの受信ウィンドウが
    閾値 32767 に届かなくなる) と WINDOW_UPDATE を送出できず接続が恒久的に
    停止する。

    nghttp2 が WINDOW_UPDATE を積むのは返却量が閾値以上のときだけなので、
    蓄積上限を閾値 + 1 (32768) にして拒否量が閾値を超えるようにする。
    """
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_pre_accept_buffer_limit = _WINDOW_UPDATE_THRESHOLD + 1
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # 受理前の楽観的カプセルは未消費の記録として残る (前提の確認)
    client.send_datagram(session_id, b"a" * 64)
    wire = client.send()
    assert wire is not None
    server.receive(wire)
    assert server._test_unconsumed_recv_bytes(session_id) is not None, (
        "受理前の楽観的カプセルが未消費の記録に残っていない"
    )

    # 蓄積上限を超える楽観送信をする (カプセルのフレーミング込み)
    client.send_datagram(session_id, b"x" * 32769)
    wire = client.send()
    assert wire is not None
    server.receive(wire)

    # 413 で拒否され、セッションは確立しない
    assert server.get_session_ids() == []
    # 未消費受信バイトの記録が解放されている
    assert server._test_unconsumed_recv_bytes(session_id) is None, (
        "413 拒否後に未消費受信バイトの記録が残っている"
    )
    # 破棄したバイトは消費済みとして扱われ、未返却分は WINDOW_UPDATE の閾値
    # 未満に留まる。nghttp2 は閾値に達した分だけを WINDOW_UPDATE で返すため、
    # 拒否の時点で返した分を除いた残り (拒否後に届いた最終フレームの分) だけが
    # 未返却として残る。返していなければ拒否した全量 (32 KiB 超) が未返却の
    # まま残り、閾値に到達しないため WINDOW_UPDATE を二度と送出できない
    effective = server._test_effective_recv_data_length()
    assert effective is not None and effective < _WINDOW_UPDATE_THRESHOLD, (
        f"破棄したバイトのコネクションレベル受信ウィンドウが返っていない: {effective}"
    )
    # 実際に WINDOW_UPDATE (Stream ID 0) が送出される
    wire = server.send()
    assert wire is not None, "413 応答と WINDOW_UPDATE が送出されていない"
    assert _has_connection_window_update(wire), "WINDOW_UPDATE が送出されていない"

    # 413 応答が届き、拒否されたことを確認する (前提の確認)
    ret = client.receive(wire)
    assert ret > 0, "413 応答の受信に失敗しました"
    rejected = [e for e in _drain_events(client) if e.type == h2.EventType.SESSION_REJECTED]
    assert len(rejected) == 1, "SESSION_REJECTED が 0 回または複数回発火しました"
    assert rejected[0].status_code == 413


def test_peer_end_stream_releases_unconsumed_recv_bytes() -> None:
    """ピアの END_STREAM によるセッション終了で未消費受信バイトの記録が解放されることを確認

    handle_end_stream は自側の END_STREAM を送らないため両ハーフが閉じず、
    on_stream_close_callback も到着しない (ストリームは half-closed (remote)
    のまま接続終了まで残る)。エントリ削除時に記録を解放しないと、その間ずっと
    残る。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_incomplete_capsule(server, session_id)
    # 空の DATA + END_STREAM でピアがストリームを閉じる (draft-15 Section 3.4)
    ret = server.receive(_encode_data_frame(session_id, b"", end_stream=True))
    assert ret > 0, "END_STREAM の注入に失敗しました"

    assert server._test_unconsumed_recv_bytes(session_id) is None, (
        "END_STREAM によるセッション終了後に未消費受信バイトの記録が残っている"
    )


def test_local_close_session_releases_unconsumed_recv_bytes() -> None:
    """ローカル close_session で未消費受信バイトの記録が解放されることを確認

    close_session 後は is_terminated が立ち、on_data_chunk_recv_callback の
    早期 return により consume_recv_bytes の経路が塞がる。ピアが END_STREAM を
    返さなければ on_stream_close_callback も到達しないため、close_session の
    時点で記録を解放する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_incomplete_capsule(server, session_id)
    server.close_session(session_id, 0, "")
    assert server._test_unconsumed_recv_bytes(session_id) is None, (
        "close_session 後に未消費受信バイトの記録が残っている"
    )


def test_2xx_response_releases_unconsumed_recv_bytes() -> None:
    """2xx 応答の送出で受理前に蓄積した未消費受信バイトの記録が解放されることを確認

    accept_session は 200 固定のため、2xx 非 200 応答は reject_session で
    生成する (受理前のセッションに限る。受理済みに呼ぶのは誤用)。この経路は
    wt_sessions_ のエントリを残したまま is_terminated を立てて蓄積を破棄する
    ため、ピアの END_STREAM を待つ間も記録が残る。
    """
    client, server = _create_h2_session_pair()

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # 受理していないためセッションは確立しない (前提の確認)
    assert server.get_session_ids() == []

    # 受理前の楽観的カプセルは未消費の記録として残る (前提の確認)
    client.send_datagram(session_id, b"a" * 64)
    wire = client.send()
    assert wire is not None
    server.receive(wire)
    assert server._test_unconsumed_recv_bytes(session_id) is not None, (
        "受理前の楽観的カプセルが未消費の記録に残っていない"
    )

    # 2xx 応答 (200 固定の accept_session では生成できない 204) を送出する
    server.reject_session(session_id, 204)
    assert server._test_unconsumed_recv_bytes(session_id) is None, (
        "2xx 応答の送出後に未消費受信バイトの記録が残っている"
    )
    # 2xx 分岐を通ったことを確認する (非 2xx 分岐はエントリを削除するため
    # 未完成カプセルの保持量が None になり、エントリが残る 2xx 分岐は 0 になる)
    assert server._test_unfinished_capsule_bytes(session_id) == 0, (
        "2xx 応答がセッションを削除する分岐 (非 2xx) を通っている"
    )


def test_peer_close_session_releases_unconsumed_recv_bytes() -> None:
    """ピアの WT_CLOSE_SESSION で未消費受信バイトの記録が解放されることを確認

    未完成カプセルの末尾を満たすバイトに続けて WT_CLOSE_SESSION カプセルと
    後続カプセルを 1 つの DATA フレームで注入する。終了を学習すると
    process_capsules は後続カプセルをバッファごと破棄するため、後続カプセルの
    4 バイトが未消費の記録として残る (完成した 2 カプセルは process_capsule が
    ワイヤ長を消費する)。記録を解放しないと接続終了まで残る。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_incomplete_capsule(server, session_id)
    # 未完成カプセルの末尾 + WT_CLOSE_SESSION (エラーコード 0) + 後続カプセル
    tail = b"abcd" * 3
    close_capsule = _encode_wt_close_session_capsule(0, "")
    trailing_capsule = _encode_capsule(0x00, b"zz")
    ret = server.receive(_encode_data_frame(session_id, tail + close_capsule + trailing_capsule))
    assert ret > 0, "WT_CLOSE_SESSION カプセルの注入に失敗しました"
    _drain_events(server)

    assert server._test_unconsumed_recv_bytes(session_id) is None, (
        "WT_CLOSE_SESSION によるセッション終了後に未消費受信バイトの記録が残っている"
    )


def test_h2_stream_reset_releases_unconsumed_recv_bytes() -> None:
    """HTTP/2 ストリームのリセットで未消費受信バイトの記録が解放されることを確認

    受信の途中でストリームがリセットされると on_stream_close_callback が
    発火する。記録を解放しないと接続終了まで残る。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    _inject_incomplete_capsule(server, session_id)
    ret = server.receive(_encode_rst_stream_frame(session_id))
    assert ret > 0, "RST_STREAM の注入に失敗しました"

    assert server._test_unconsumed_recv_bytes(session_id) is None, (
        "ストリームのリセット後に未消費受信バイトの記録が残っている"
    )


def test_many_sessions_do_not_accumulate_unconsumed_recv_bytes() -> None:
    """長命コネクションで多数のセッションを終了しても記録が蓄積しないことを確認

    未完成カプセルを受信したセッションを終了させ続けても、記録が残らない
    ことを確認する。解放しないと wt_sessions_ のエントリが消えても
    unconsumed_recv_bytes_ のエントリだけが接続終了まで単調増加する。
    """
    client, server = _create_h2_session_pair()

    # 同時に生かしたセッションの記録が独立していることを確認する
    # (片方の解放がもう片方の記録を消さない)
    first = _connect_h2_session(client, server)
    second = _connect_h2_session(client, server)
    _inject_incomplete_capsule(server, first)
    _inject_incomplete_capsule(server, second)
    server.close_session(first, 0, "")
    assert server._test_unconsumed_recv_bytes(first) is None
    assert server._test_unconsumed_recv_bytes(second) is not None, (
        "他セッションの終了で無関係な記録まで解放されている"
    )
    server.close_session(second, 0, "")

    for _ in range(20):
        session_id = _connect_h2_session(client, server)
        _inject_incomplete_capsule(server, session_id)

        server.close_session(session_id, 0, "")
        assert server._test_unconsumed_recv_bytes(session_id) is None, (
            "セッション終了後に未消費受信バイトの記録が残っている"
        )
