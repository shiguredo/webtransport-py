"""WebTransport over HTTP/2 のデータグラム送信テスト

Sans-IO 構成 (conftest.py の h2 用 Sans-IO ヘルパー) を使い、セッション終了
後の send_datagram が無視されることを検証する。セッション終了の経路
(WT_CLOSE_SESSION 受信 / ローカル close_session) の両方で検証する。
h2 仕様 (draft-ietf-webtrans-http2-15) には h3 の Section 6 相当の「新しい
データグラムを送信してはならない MUST」は存在しないが、Section 6.12 の
「WT_CLOSE_SESSION を受信したら END_STREAM で応答してストリームを閉じる
MUST」と Section 3.4 の「セッション終了 = CONNECT ストリームのクローズ」に
より、受信後は終了を学習した状態とみなせる。本対応は仕様強制ではなく
実装ポリシーである。
"""

from __future__ import annotations

from conftest import (
    _connect_h2_session,
    _create_h2_session_pair,
    _h2_pump,
)

from webtransport import h2


def _drain_events(session: h2.Session) -> list[h2.Event]:
    """セッションに積まれたイベントを全て取り出す"""
    events = []
    while True:
        event = session.next_event()
        if event is None:
            break
        events.append(event)
    return events


def _encode_capsule(capsule_type: int, payload: bytes) -> bytes:
    """Capsule Protocol のカプセルバイト列を組み立てる (RFC 9297 Section 3.2)

    テストで使う小さい値のみ対応する (Type / Length とも 1 バイト varint)。
    HTTP/2 DATA フレームのペイロードはカプセルバイト列そのもののため、
    ワイヤデータに対する部分列チェックで送出を検証できる。テストのペイロード
    は 64 バイト未満でありフレーム分割は起きない。DATAGRAM capsule の Type は
    0x00 である。
    """
    assert capsule_type < 0x40 and len(payload) < 0x40
    return bytes([capsule_type, len(payload)]) + payload


def test_send_datagram_after_recv_wt_close_session_ignored() -> None:
    """WT_CLOSE_SESSION 受信後に send_datagram が無視されることを確認

    HTTP/2 ストリームは両ハーフが閉じるまで残るため、受信側の終了フラグが
    無ければデータグラムカプセルがワイヤへ送出されてしまう (修正前の挙動)。
    本テストは修正前実装では失敗する (送出されたデータグラムがピアで処理され
    イベント化する)。送出抑止 (終了フラグ) の回帰検証を担う。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアントが WT_CLOSE_SESSION を送出し、サーバーが受信する
    client.close_session(session_id, 0)
    _h2_pump(client, server)

    # サーバー側で SessionClosed が発火している
    closed_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == session_id

    # WT_CLOSE_SESSION 受信後の send_datagram はピアに届かない
    server.send_datagram(session_id, b"after-close")
    _h2_pump(server, client)
    assert all(event.type != h2.EventType.DATAGRAM for event in _drain_events(client))


def test_send_datagram_after_local_close_session_ignored() -> None:
    """ローカル close_session 後 (flush 前) に send_datagram が無視されることを確認

    close_session (WT_CLOSE_SESSION 送出) 後に終了フラグが立つため、flush の
    タイミング (send() 呼び出し) に依存せず、データグラムカプセルが
    WT_CLOSE_SESSION の後ろに積まれない (修正前はタイミング依存で送出され
    得た)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ローカル close_session を呼ぶ (flush はまだ)
    client.close_session(session_id, 0)

    # flush 前の send_datagram は無視される。送出抑止の回帰検証は受信経路の
    # テストが担い、本テストと flush 後テストは完了条件 (flush 前・後
    # どちらでも送出されない) の動作確認である
    client.send_datagram(session_id, b"after-close")
    _h2_pump(client, server)

    # ピアには WT_CLOSE_SESSION 由来の SessionClosed のみが届き、
    # データグラムは届かない
    events = _drain_events(server)
    assert all(event.type != h2.EventType.DATAGRAM for event in events)
    closed_events = [event for event in events if event.type == h2.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1


def test_send_datagram_after_local_close_session_flushed_ignored() -> None:
    """ローカル close_session 後 (flush 後) に send_datagram が無視されることを確認

    WT_CLOSE_SESSION 送出後の flush (send() 呼び出し) 完了後も終了フラグが
    立ったままのため、以後の send_datagram は無視される (完了条件の
    「flush 前・後どちらでも」に対応する)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ローカル close_session を呼び、flush まで完了させる
    client.close_session(session_id, 0)
    _h2_pump(client, server)

    # flush 後の send_datagram は無視され、ピアに届かない
    client.send_datagram(session_id, b"after-flush")
    _h2_pump(client, server)
    assert all(event.type != h2.EventType.DATAGRAM for event in _drain_events(server))


def test_open_stream_fails_after_local_close_session() -> None:
    """ローカル close_session 後に open_stream が失敗し、get_session_ids から消えることを確認

    close_session は is_established も false にする (受信側の
    handle_wt_close_session と対称)。セッション終了後に新規ストリームが
    開かれず、get_session_ids にも残らない (h3 側の close_stream による
    session_ids_ からの削除と対称の挙動)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # close_session 前は open_stream が成功し、get_session_ids に含まれる
    assert client.open_stream(session_id, False) >= 0
    assert client.get_session_ids() == [session_id]

    # ローカル close_session 後は open_stream が失敗し、ID が消える
    client.close_session(session_id, 0)
    assert client.open_stream(session_id, False) == -1
    assert client.get_session_ids() == []


def test_send_datagram_alive_session_delivered() -> None:
    """生存セッションの send_datagram は従来どおり送出されることを確認

    終了後の送信抑止は終了を学習したセッションにのみ適用され、生存セッション
    への送信は影響を受けない。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # 送信側 (クライアント) でデータグラムが送出され、ピア (サーバー) に
    # 届いて Datagram イベントになる
    client.send_datagram(session_id, b"hello")
    _h2_pump(client, server)

    datagram_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.DATAGRAM
    ]
    assert len(datagram_events) == 1
    assert datagram_events[0].session_id == session_id
    assert datagram_events[0].data == b"hello"


def test_send_datagram_unestablished_session_id_ignored() -> None:
    """一度も connect されていないセッション ID への送信が無視されることを確認

    エントリ不在のセッション ID への送信はピアに届かない (回帰確認)。
    本テストが検証できるのは「ピアに届かない」ことのみであり、バッファへの
    残留は内部状態のため公開 API からは観測できない (send_capsule が
    http2_stream_buffers_ にエントリを新規生成していた修正前でも、存在しない
    HTTP/2 ストリームへの resume_data は失敗してワイヤ送出されなかった)。
    ガードの存在下での無害性の確認に留める。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # 確立済み ID とは異なる、一度も connect されていない ID への送信
    # (h2 のセッション ID は HTTP/2 ストリーム ID。+1 はサーバー起動
    # ストリーム ID であり、このテストでサーバー起動 CONNECT は存在しない
    # ためエントリ不在になる)
    unestablished_session_id = session_id + 1
    client.send_datagram(unestablished_session_id, b"never-established")
    _h2_pump(client, server)

    # ピアに何も届かない
    assert all(event.type != h2.EventType.DATAGRAM for event in _drain_events(server))


def test_send_datagram_alive_after_other_session_closed_delivered() -> None:
    """一方のセッション終了後も、生存セッションへの送信は継続されることを確認

    終了フラグは WtSessionInfo 単位で管理されるため、一方のセッションの
    終了が同一接続の他の生存セッションへの送信に影響しない。
    """
    client, server = _create_h2_session_pair()
    first_session_id = _connect_h2_session(client, server)
    second_session_id = _connect_h2_session(client, server)

    # 1 つ目のセッションを終了する
    client.close_session(first_session_id, 0)

    # 生存セッション (2 つ目) への送信は従来どおり送出され、ピアに届く
    client.send_datagram(second_session_id, b"alive")
    _h2_pump(client, server)
    datagram_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.DATAGRAM
    ]
    assert len(datagram_events) == 1
    assert datagram_events[0].session_id == second_session_id
    assert datagram_events[0].data == b"alive"


def test_send_datagram_client_optimistic_delivered() -> None:
    """サーバー応答前の楽観的データグラム送信が妨げられないことを確認

    draft-15 Section 3.2 の MAY「クライアントは応答を待たずに WebTransport
    カプセル (データグラムはその例) を送信してよい」。connect 直後
    (200 応答前) は is_established が false だが、終了状態の判定に
    is_established を使わないため、DATAGRAM capsule がワイヤに送出される。
    受理前のデータはサーバーが処理しない (Section 3.2 の MUST) ため、
    ワイヤ上の DATAGRAM capsule の存在で送出を検証する。
    """
    client, _ = _create_h2_session_pair()

    # CONNECT リクエストを送信する (サーバー応答はまだ)
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0

    # サーバー応答前でも DATAGRAM capsule がワイヤに送出される
    client.send_datagram(session_id, b"optimistic")
    wire = client.send()
    assert wire is not None
    assert _encode_capsule(0x00, b"optimistic") in wire


def test_send_datagram_server_optimistic_delivered() -> None:
    """サーバー側の accept 前データグラム送信が妨げられないことを確認

    draft-15 Section 3.2 の楽観的送信 MAY はクライアントのみに定められており、
    サーバー側は仕様に禁止がない。サーバーは CONNECT リクエスト受信時に
    wt_sessions_ へエントリが挿入され、accept 前でも終了フラグが立っていない
    ため send_datagram は無視されない。HTTP/2 では応答 (200) を送信するまで
    DATA フレームを送れないため、受理後にキュー済みの DATAGRAM capsule が
    送出されてピアに届く (accept 前の送信が塞がれないことの検証)。
    """
    client, server = _create_h2_session_pair()

    # クライアントが CONNECT を送信し、サーバーがリクエストを受信する (accept 前)
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    _h2_pump(client, server)

    # サーバー側で SESSION_READY が発火している (accept_session は未実施)
    ready_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1

    # accept 前の send_datagram は無視されず、受理後にピアへ届く
    server.send_datagram(session_id, b"server-optimistic")
    assert server.accept_session(session_id) is True
    _h2_pump(server, client)
    datagram_events = [
        event for event in _drain_events(client) if event.type == h2.EventType.DATAGRAM
    ]
    assert len(datagram_events) == 1
    assert datagram_events[0].data == b"server-optimistic"
