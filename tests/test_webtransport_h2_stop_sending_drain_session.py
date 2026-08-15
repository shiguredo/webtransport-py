"""WebTransport over HTTP/2 の stop_sending / drain_session の終了後送出抑止テスト

セッション終了後 (WT_CLOSE_SESSION 受信 / ピアの END_STREAM 受信 / ローカル
close_session 後 / クライアントの非 2xx 拒否受信後) に stop_sending /
drain_session を呼んでも、終了済みセッション宛の WT_STOP_SENDING /
WT_DRAIN_SESSION capsule がワイヤへ送出されないことを検証する。修正前はエントリ
不在・終了済みを確認せず send_capsule を呼ぶため、ワイヤへ送出される経路
(ピアの END_STREAM 受信後・ローカル close_session 後の flush 前・非 2xx 拒否
受信後) と、http2_stream_buffers_ に残留し得る経路 (WT_CLOSE_SESSION 受信後)
があった。残留は内部状態のため公開 API からは観測できない (既存テストの
docstring も同旨) ため、本テストは送出の有無で検証する。サーバー側の
reject_session 2xx 送出経路はデータプロバイダ未登録のため修正前後とも送出
されないことからテスト対象外。
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


def _encode_wt_stop_sending_capsule(stream_id: int, error_code: int) -> bytes:
    """WT_STOP_SENDING capsule のワイヤバイト列を組み立てる

    Type 0x190B4D3A (4 バイト varint) + Length + Stream ID (varint) +
    Error Code (varint)。Length は 1 バイト varint のみ対応する (テストで
    使う小さい値のみ。64 バイト未満のペイロード前提)。HTTP/2 DATA フレームの
    ペイロードはカプセルバイト列そのもののため、ワイヤデータに対する部分列
    チェックで送出を検証できる。
    """
    payload = _encode_varint(stream_id) + _encode_varint(error_code)
    return bytes([0x99, 0x0B, 0x4D, 0x3A, len(payload)]) + payload


def _encode_wt_drain_session_capsule() -> bytes:
    """WT_DRAIN_SESSION capsule のワイヤバイト列を組み立てる

    Type 0x78AE は QUIC varint では 4 バイト (0x40000000 未満のため
    [0x80, 0x00, 0x78, 0xAE]) + Length 0 (空ペイロード)。ワイヤデータに
    対する部分列チェックで送出を検証できる。
    """
    return b"\x80\x00\x78\xae\x00"


def _encode_data_frame(session_id: int, payload: bytes = b"", end_stream: bool = False) -> bytes:
    """DATA フレームのワイヤバイト列を組み立てる

    END_STREAM フラグ (0x01) 付きでピアがストリームを閉じた場合を再現する。
    h2 の公開 API に WT_CLOSE_SESSION なしで END_STREAM のみを送出する手段が
    存在しないため、ワイヤ注入で再現する。
    """
    flags = 0x01 if end_stream else 0x00
    return (
        len(payload).to_bytes(3, "big")
        + bytes([0x00, flags])
        + (session_id & 0x7FFFFFFF).to_bytes(4, "big")
        + payload
    )


def test_stop_sending_after_peer_end_stream_not_sent() -> None:
    """ピアの END_STREAM 受信後に stop_sending がワイヤへ送出されないことを確認

    ピアが END_STREAM のみで CONNECT ストリームを閉じた場合 (draft-15
    Section 3.4 の正規の終了経路)、handle_end_stream がエントリを削除する。
    エントリ削除後も自側の END_STREAM 応答は送出されない (既知の制約) ため
    HTTP/2 ストリームは half-closed (remote) で生存し、nghttp2_session_resume_data
    が成功する。修正前はエントリ不在でもカプセルをキューしてワイヤへ送出
    されていた (終了済みセッション宛の誤送出)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # ピア (サーバー) が END_STREAM のみでストリームを閉じる
    ret = client.receive(_encode_data_frame(session_id, end_stream=True))
    assert ret > 0, "END_STREAM フレームの注入に失敗しました"

    # END_STREAM 検知後の stop_sending はワイヤへ送出されない
    client.stop_sending(session_id, stream_id, 42)
    wire = client.send()
    assert wire is None or _encode_wt_stop_sending_capsule(stream_id, 42) not in wire


def test_drain_session_after_peer_end_stream_not_sent() -> None:
    """ピアの END_STREAM 受信後に drain_session がワイヤへ送出されないことを確認

    stop_sending と同じく、END_STREAM 検知 (draft-15 Section 3.4) 後の
    drain_session はエントリ不在で塞がれる。修正前はカプセルをキューして
    ワイヤへ送出されていた。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピア (サーバー) が END_STREAM のみでストリームを閉じる
    ret = client.receive(_encode_data_frame(session_id, end_stream=True))
    assert ret > 0, "END_STREAM フレームの注入に失敗しました"

    # END_STREAM 検知後の drain_session はワイヤへ送出されない
    client.drain_session(session_id)
    wire = client.send()
    assert wire is None or _encode_wt_drain_session_capsule() not in wire


def test_stop_sending_after_local_close_session_not_sent() -> None:
    """ローカル close_session 後 (flush 前) の stop_sending がワイヤへ送出されないことを確認

    close_session は flush のタイミングに依存せず is_terminated を立てる。
    修正前は WT_STOP_SENDING capsule が WT_CLOSE_SESSION の後ろに積まれて
    flush で送出され得た (終了済みセッション宛の誤送出)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # ローカル close_session を呼ぶ (flush はまだ)
    client.close_session(session_id, 0)

    # flush 前の stop_sending はワイヤへ送出されない
    client.stop_sending(session_id, stream_id, 42)
    wire = client.send()
    assert wire is None or _encode_wt_stop_sending_capsule(stream_id, 42) not in wire


def test_drain_session_after_local_close_session_not_sent() -> None:
    """ローカル close_session 後 (flush 前) の drain_session がワイヤへ送出されないことを確認

    stop_sending と同じく、close_session 後の drain_session は is_terminated
    で塞がれる。修正前は WT_DRAIN_SESSION capsule が WT_CLOSE_SESSION の後ろに
    積まれて flush で送出され得た。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ローカル close_session を呼ぶ (flush はまだ)
    client.close_session(session_id, 0)

    # flush 前の drain_session はワイヤへ送出されない
    client.drain_session(session_id)
    wire = client.send()
    assert wire is None or _encode_wt_drain_session_capsule() not in wire


def test_stop_sending_after_client_non_2xx_reject_not_sent() -> None:
    """非 2xx 拒否受信後に stop_sending がワイヤへ送出されないことを確認

    サーバーが reject_session (非 2xx) で拒否すると、クライアントのエントリが
    応答受信時に削除される (draft-15 Section 3.2 では非 2xx はセッション非
    確立)。HTTP/2 ストリーム自体はサーバー側のみが閉じた半開きで生存する
    ため、修正前はエントリ不在でもカプセルをキューしてワイヤへ送出されて
    いた。stream_id は送出抑止の検証に無関係のため 0 を使う (拒否前はセッション
    未確立で open_stream できない)。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが 403 で拒否する
    server.reject_session(session_id, 403)
    _h2_pump(server, client)

    # 拒否後の stop_sending はワイヤへ送出されない
    client.stop_sending(session_id, 0, 42)
    wire = client.send()
    assert wire is None or _encode_wt_stop_sending_capsule(0, 42) not in wire


def test_drain_session_after_client_non_2xx_reject_not_sent() -> None:
    """非 2xx 拒否受信後に drain_session がワイヤへ送出されないことを確認

    stop_sending と同じく、非 2xx 拒否 (draft-15 Section 3.2) でエントリが
    削除された後の drain_session は送出されない。修正前はカプセルをキュー
    してワイヤへ送出されていた。
    """
    client, server = _create_h2_session_pair()
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバーが 403 で拒否する
    server.reject_session(session_id, 403)
    _h2_pump(server, client)

    # 拒否後の drain_session はワイヤへ送出されない
    client.drain_session(session_id)
    wire = client.send()
    assert wire is None or _encode_wt_drain_session_capsule() not in wire


def test_stop_sending_after_recv_wt_close_session_not_sent() -> None:
    """WT_CLOSE_SESSION 受信後の stop_sending がワイヤへ送出されないことを確認 (送出なしのピン)

    handle_wt_close_session はエントリを削除して END_STREAM 応答を送出する
    ため、修正前後ともワイヤへの送出は発生しない (修正前は消えた
    http2_stream_buffers_ エントリが再生成されてカプセルが残留した。
    on_stream_close_callback のバッファ破棄はストリームクローズ時に 1 回
    だけ発火するため、クローズ後にキューされたカプセルは破棄されずメモリを
    保持し続ける。残留は内部状態のため公開 API からは観測できない)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # ピア (クライアント) が WT_CLOSE_SESSION を送り、サーバーが受信する
    client.close_session(session_id, 0)
    _h2_pump(client, server)

    # 受信後の stop_sending はワイヤへ送出されない
    server.stop_sending(session_id, stream_id, 42)
    wire = server.send()
    assert wire is None or _encode_wt_stop_sending_capsule(stream_id, 42) not in wire


def test_drain_session_after_recv_wt_close_session_not_sent() -> None:
    """WT_CLOSE_SESSION 受信後の drain_session がワイヤへ送出されないことを確認 (送出なしのピン)

    stop_sending と同じく、修正前後ともワイヤへの送出は発生しない (修正前は
    バッファ残留のみ。内部状態のため公開 API からは観測できない)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ピア (クライアント) が WT_CLOSE_SESSION を送り、サーバーが受信する
    client.close_session(session_id, 0)
    _h2_pump(client, server)

    # 受信後の drain_session はワイヤへ送出されない
    server.drain_session(session_id)
    wire = server.send()
    assert wire is None or _encode_wt_drain_session_capsule() not in wire


def test_stop_sending_alive_session_delivered() -> None:
    """生存セッションの stop_sending は従来どおり送出されることを確認

    ガードは終了を学習したセッションにのみ適用され、生存セッションへの
    stop_sending はピアに届いて StopSending イベントになる (回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # クライアントの stop_sending がサーバーに届いてイベントになる
    client.stop_sending(session_id, stream_id, 42)
    _h2_pump(client, server)
    stop_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STOP_SENDING
    ]
    assert len(stop_events) == 1
    assert stop_events[0].session_id == session_id
    assert stop_events[0].stream_id == stream_id
    assert stop_events[0].error_code == 42


def test_drain_session_alive_session_delivered() -> None:
    """生存セッションの drain_session は従来どおり送出されることを確認

    ガードは終了を学習したセッションにのみ適用され、生存セッションへの
    drain_session はピアに届いて SessionDraining イベントになる (回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアントの drain_session がサーバーに届いてイベントになる
    client.drain_session(session_id)
    _h2_pump(client, server)
    draining_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_DRAINING
    ]
    assert len(draining_events) == 1
    assert draining_events[0].session_id == session_id


def test_stop_sending_unestablished_session_id_ignored() -> None:
    """一度も connect されていないセッション ID への stop_sending が無視されることを確認

    エントリ不在のセッション ID への送信はピアに届かない (回帰確認)。
    本テストが検証できるのは「ワイヤに送出されない」ことのみであり、バッファ
    への残留は内部状態のため公開 API からは観測できない (send_capsule が
    http2_stream_buffers_ にエントリを新規生成していた修正前でも、存在しない
    HTTP/2 ストリームへの resume_data は失敗してワイヤ送出されなかった)。
    ガードの存在下での無害性の確認に留める (send_datagram の既存テストと対称)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # 確立済み ID とは異なる、一度も connect されていない ID への送信
    # (h2 のセッション ID は HTTP/2 ストリーム ID。+1 はサーバー起動
    # ストリーム ID であり、このテストでサーバー起動 CONNECT は存在しない
    # ためエントリ不在になる)
    unestablished_session_id = session_id + 1
    client.stop_sending(unestablished_session_id, 0, 42)
    wire = client.send()
    assert wire is None or _encode_wt_stop_sending_capsule(0, 42) not in wire


def test_drain_session_unestablished_session_id_ignored() -> None:
    """一度も connect されていないセッション ID への drain_session が無視されることを確認

    stop_sending と同じく、エントリ不在のセッション ID への送信はワイヤに
    送出されない (ガードの存在下での無害性の確認に留める)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # 確立済み ID とは異なる、一度も connect されていない ID への送信
    unestablished_session_id = session_id + 1
    client.drain_session(unestablished_session_id)
    wire = client.send()
    assert wire is None or _encode_wt_drain_session_capsule() not in wire
