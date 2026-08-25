"""WebTransport over HTTP/2 の send_stream_data / reset_stream の終了後送出抑止テスト

ローカル close_session 後 (flush 前) に send_stream_data / reset_stream を
呼んでも、終了済みセッション宛の WT_STREAM / WT_RESET_STREAM capsule が
ワイヤへ送出されないことと、FIN 送出後・リセット送出後のストリームへの
再送信が塞がれることを検証する。close_session はエントリを残したまま
is_terminated を立てるため、修正前は get_wt_session の確認だけでは塞がれず、
flush 前はカプセルが WT_CLOSE_SESSION の後ろに積まれてワイヤへ送出され、
flush 後は http2_stream_buffers_ に残留していた。残留は内部状態のため公開
API からは観測できない (stop_sending / drain_session のテスト docstring も
同旨) ため、本テストは送出の有無で検証する。WT_CLOSE_SESSION 受信後・
ピアの END_STREAM 受信後・クライアントの非 2xx 拒否受信後はエントリ削除で
塞がれるため修正前後とも送出されず、サーバー側の reject_session 2xx 送出
経路はデータプロバイダ未登録のため修正前後とも送出されないことから、いずれ
もテスト対象外。
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
    HTTP/2 DATA フレームのペイロードはカプセルバイト列そのもののため、
    ワイヤデータに対する部分列チェックで送出を検証できる。
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
    HTTP/2 DATA フレームのペイロードはカプセルバイト列そのもののため、
    ワイヤデータに対する部分列チェックで送出を検証できる。
    """
    payload = _encode_varint(stream_id) + _encode_varint(error_code) + _encode_varint(reliable_size)
    assert len(payload) < 0x40, "Length が 1 バイト varint に収まる前提が崩れています"
    return bytes([0x99, 0x0B, 0x4D, 0x39, len(payload)]) + payload


def test_send_stream_data_after_local_close_session_not_sent() -> None:
    """ローカル close_session 後 (flush 前) の send_stream_data がワイヤへ送出されないことを確認

    close_session は flush のタイミングに依存せず is_terminated を立てる。
    修正前は WT_STREAM capsule が WT_CLOSE_SESSION の後ろに積まれて flush
    で送出され得た (終了済みセッション宛の誤送出)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # ローカル close_session を呼ぶ (flush はまだ)
    client.close_session(session_id, 0)

    # flush 前の send_stream_data はワイヤへ送出されない。close_session の
    # WT_CLOSE_SESSION はキュー済みのためワイヤは必ず非 None
    client.send_stream_data(session_id, stream_id, b"after-close")
    wire = client.send()
    assert wire is not None
    assert _encode_wt_stream_capsule(stream_id, b"after-close") not in wire


def test_send_stream_data_fin_after_local_close_session_not_sent() -> None:
    """ローカル close_session 後 (flush 前) の fin 付き send_stream_data がワイヤへ送出されないことを確認

    fin は WT_STREAM_FIN capsule の送出を意味するが、ガードは capsule 種別の
    選択より前に置かれているため、fin の有無に関わらず終了済みセッションへ
    は送出されない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # ローカル close_session を呼ぶ (flush はまだ)
    client.close_session(session_id, 0)

    # flush 前の fin 付き send_stream_data はワイヤへ送出されない
    client.send_stream_data(session_id, stream_id, b"after-close", fin=True)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_stream_capsule(stream_id, b"after-close", fin=True) not in wire


def test_reset_stream_after_local_close_session_not_sent() -> None:
    """ローカル close_session 後 (flush 前) の reset_stream がワイヤへ送出されないことを確認

    close_session は flush のタイミングに依存せず is_terminated を立てる。
    修正前は WT_RESET_STREAM capsule が WT_CLOSE_SESSION の後ろに積まれて
    flush で送出され得た (終了済みセッション宛の誤送出)。reset_stream は
    ストリームの有無に関わらずカプセルをキューするため、送出抑止の検証に
    stream_id は無関係。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # ローカル close_session を呼ぶ (flush はまだ)
    client.close_session(session_id, 0)

    # flush 前の reset_stream はワイヤへ送出されない。close_session の
    # WT_CLOSE_SESSION はキュー済みのためワイヤは必ず非 None
    client.reset_stream(session_id, stream_id, 42)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_reset_stream_capsule(stream_id, 42, 0) not in wire


def test_send_stream_data_alive_session_delivered() -> None:
    """生存セッションの send_stream_data は従来どおり送出されることを確認

    ガードは終了を学習したセッションにのみ適用され、生存セッションへの
    send_stream_data はピアに届いて StreamData イベントになる (回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # クライアントの send_stream_data がサーバーに届いてイベントになる
    client.send_stream_data(session_id, stream_id, b"alive")
    _h2_pump(client, server)
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 1
    assert stream_events[0].session_id == session_id
    assert stream_events[0].stream_id == stream_id
    assert stream_events[0].data == b"alive"


def test_reset_stream_alive_session_delivered() -> None:
    """生存セッションの reset_stream は従来どおり送出されることを確認

    ガードは終了を学習したセッションにのみ適用され、生存セッションへの
    reset_stream はピアに届いて StreamReset イベントになる (回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # クライアントの reset_stream がサーバーに届いてイベントになる
    client.reset_stream(session_id, stream_id, 42)
    _h2_pump(client, server)
    reset_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_RESET
    ]
    assert len(reset_events) == 1
    assert reset_events[0].session_id == session_id
    assert reset_events[0].stream_id == stream_id
    assert reset_events[0].error_code == 42


def test_send_stream_data_after_fin_not_sent() -> None:
    """FIN 送出後の send_stream_data が塞がれることを確認

    FIN (WT_STREAM_FIN) 送出後は送信側状態を DataSent へ遷移させ、以後の
    send_stream_data を無視する (draft-15 Section 6.4 の「A WT_STREAM
    capsule MUST NOT be sent after a stream is closed or reset」)。修正前は
    FIN 後に再度 send_stream_data を呼ぶと閉じたストリームへの WT_STREAM が
    ワイヤへ送出され、ピアから WT_STREAM_STATE_ERROR を受けた。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # FIN 付きで送信する (ワイヤには WT_STREAM_FIN capsule が積まれる)
    client.send_stream_data(session_id, stream_id, b"hello", fin=True)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_stream_capsule(stream_id, b"hello", fin=True) in wire

    # FIN 後の再送信はワイヤへ送出されない (送出物は何も残らない)
    client.send_stream_data(session_id, stream_id, b"again")
    wire = client.send()
    assert wire is None


def test_reset_stream_after_fin_not_sent() -> None:
    """FIN 送出後の reset_stream が塞がれることを確認

    FIN 送出後 (DataSent 相当) のストリームは送信側から見て閉じているため、
    WT_RESET_STREAM の送出も塞ぐ (draft-15 Section 6.2 の「A
    WT_RESET_STREAM capsule MUST NOT be sent after a stream is closed or
    reset」)。修正前は FIN 後に reset_stream を呼ぶと WT_RESET_STREAM が
    ワイヤへ送出され、ピア (サーバー側の DataRecvd 検知) がセッション
    エラーにした。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # FIN 付きで送信する
    client.send_stream_data(session_id, stream_id, b"hello", fin=True)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_stream_capsule(stream_id, b"hello", fin=True) in wire

    # FIN 後の reset_stream はワイヤへ送出されない (送出物は何も残らない。
    # reliable_size は省略時 0 であり、仮に送出された場合の Reliable Size
    # は FIN 送出時の bytes_sent (= b"hello" の長さ = 5) へフォールバック
    # するが、ガードにより送出される前に返る)
    client.reset_stream(session_id, stream_id, 1)
    wire = client.send()
    assert wire is None


def test_reset_stream_after_reset_not_sent() -> None:
    """リセット送出後の再 reset_stream が塞がれることを確認

    リセット送出後 (ResetSent) のストリームへの再 reset_stream は無視する
    (draft-15 Section 6.2 の MUST NOT。修正前は WT_RESET_STREAM が二重に
    ワイヤへ送出された)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # 1 回目の reset_stream で WT_RESET_STREAM が送出される
    client.reset_stream(session_id, stream_id, 1)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_reset_stream_capsule(stream_id, 1, 0) in wire

    # 2 回目の reset_stream はワイヤへ送出されない (送出物は何も残らない)
    client.reset_stream(session_id, stream_id, 2)
    wire = client.send()
    assert wire is None


def test_empty_fin_then_send_not_sent() -> None:
    """空の起動 WT_STREAM_FIN 送出後の send_stream_data が塞がれることを確認

    data 空 + fin=True の起動 WT_STREAM_FIN (ストリームを開いて同時に閉じる
    カプセル。draft-15 Section 6.4 の「Empty WT_STREAM capsules MUST NOT be
    used unless they open or close a stream」) でも DataSent へ遷移し、
    以後の send_stream_data は塞がれる。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # 空の起動 WT_STREAM_FIN を送信する
    client.send_stream_data(session_id, stream_id, b"", fin=True)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_stream_capsule(stream_id, b"", fin=True) in wire

    # 空 FIN 後の再送信はワイヤへ送出されない (送出物は何も残らない)
    client.send_stream_data(session_id, stream_id, b"again")
    wire = client.send()
    assert wire is None
