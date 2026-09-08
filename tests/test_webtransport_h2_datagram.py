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
    _drain_events,
    _encode_capsule,
    _encode_data_frame,
    _encode_varint,
    _h2_pump,
)

from webtransport import h2


def test_send_datagram_after_recv_wt_close_session_ignored() -> None:
    """WT_CLOSE_SESSION 受信後に send_datagram が無視されることを確認

    HTTP/2 ストリームは両ハーフが閉じるまで残る。WT_CLOSE_SESSION 受信処理
    (handle_wt_close_session) がエントリを削除するため、受信後の
    send_datagram はエントリ不在で塞がれる。送出抑止の回帰検証を担う
    (エントリを削除せず終了フラグのみで抑止していた修正前実装でも成立する
    設計ピン)。
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

    close_session はエントリを残したまま is_established も false にする
    (受信側の handle_wt_close_session はエントリ削除で同じ効果を得る)。
    セッション終了後に新規ストリームが開かれず、get_session_ids にも残らない
    (h3 側の close_stream による session_ids_ からの削除と対称の挙動)。
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


def test_pre_accept_datagram_same_receive_delivered_after_accept() -> None:
    """同一 receive で届いた楽観的データグラムが受理後に配信されることを確認

    CONNECT (HEADERS) と DATAGRAM カプセル (DATA) が同一 receive で届くと、
    受理前は蓄積され、accept_session 後に SessionReady に続いて Datagram が
    発火する (draft-15 Section 3.2 の楽観送信) 。
    """
    client, server = _create_h2_session_pair()

    # CONNECT 直後に楽観送信し、全出力を単一ワイヤでサーバーへ渡す (同一 receive)
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    client.send_datagram(session_id, b"optimistic-same")
    wire_parts = []
    while True:
        part = client.send()
        if part is None:
            break
        wire_parts.append(part)
    assert wire_parts
    server.receive(b"".join(wire_parts))

    # 受理前は Datagram が発火しないが SessionReady は発火している
    events = _drain_events(server)
    assert [e for e in events if e.type == h2.EventType.SESSION_READY]
    assert not [e for e in events if e.type == h2.EventType.DATAGRAM]

    # 受理後に蓄積が配信される
    assert server.accept_session(session_id) is True
    datagram_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.DATAGRAM
    ]
    assert len(datagram_events) == 1
    assert datagram_events[0].data == b"optimistic-same"


def test_pre_accept_datagram_separate_receive_delivered_after_accept() -> None:
    """別 receive で届いた楽観的データグラムが受理後に配信されることを確認

    CONNECT と DATAGRAM が別 receive で届く変種。断片再構成と同一機構の
    蓄積で扱う。
    """
    client, server = _create_h2_session_pair()

    # CONNECT のみ先に渡す (全出力を集めて単一 receive にする)
    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    wire_parts = []
    while True:
        part = client.send()
        if part is None:
            break
        wire_parts.append(part)
    assert wire_parts
    server.receive(b"".join(wire_parts))

    # 楽観送信を別の receive で渡す
    client.send_datagram(session_id, b"optimistic-separate")
    wire = client.send()
    assert wire is not None
    server.receive(wire)

    # 受理前は Datagram が発火しない
    events = _drain_events(server)
    assert not [e for e in events if e.type == h2.EventType.DATAGRAM]

    # 受理後に蓄積が配信される
    assert server.accept_session(session_id) is True
    datagram_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.DATAGRAM
    ]
    assert len(datagram_events) == 1
    assert datagram_events[0].data == b"optimistic-separate"


def test_pre_accept_stream_data_delivered_after_accept() -> None:
    """受理前のストリームデータが受理後に配信されることを確認

    ワイヤ注入で WT_STREAM カプセルを受理前に届け、accept_session 後に
    SessionReady に続いて StreamData が発火することを検証する。
    """
    client, server = _create_h2_session_pair()

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    wire = client.send()
    assert wire is not None
    server.receive(wire)

    # WT_STREAM カプセル (ストリーム 0、データ付き) を受理前に注入する
    stream_payload = _encode_varint(0) + b"stream-early"
    server.receive(_encode_data_frame(session_id, _encode_capsule(0x190B4D3C, stream_payload)))

    # 受理前は StreamData が発火しない
    events = _drain_events(server)
    assert not [e for e in events if e.type == h2.EventType.STREAM_DATA]

    # 受理後に蓄積が配信される
    assert server.accept_session(session_id) is True
    stream_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.STREAM_DATA
    ]
    assert len(stream_events) == 1
    assert stream_events[0].data == b"stream-early"


def test_pre_accept_buffer_rejected_on_discard() -> None:
    """reject_session で蓄積が破棄されることを確認"""
    client, server = _create_h2_session_pair()

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    client.send_datagram(session_id, b"optimistic-dropped")
    wire = client.send()
    assert wire is not None
    server.receive(wire)

    # 拒否すると蓄積は破棄され、以後の Datagram 配信はない
    server.reject_session(session_id, 403)
    datagram_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.DATAGRAM
    ]
    assert datagram_events == []


def test_pre_accept_buffer_overflow_rejected_413() -> None:
    """蓄積の上限超過で非 2xx (413) 拒否されることを確認"""
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_pre_accept_buffer_limit = 10
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    # 上限 10 バイトを超える楽観送信をする
    client.send_datagram(session_id, b"0123456789ABCDEF")
    wire = client.send()
    assert wire is not None
    server.receive(wire)

    # 413 応答が送出され、セッションは確立しない
    assert server.get_session_ids() == []
    _h2_pump(server, client)
    rejected = [e for e in _drain_events(client) if e.type == h2.EventType.SESSION_REJECTED]
    assert len(rejected) == 1
    assert rejected[0].status_code == 413


def test_pre_accept_close_session_single_fire() -> None:
    """受理前の WT_CLOSE_SESSION は二重発火しないことを確認"""
    client, server = _create_h2_session_pair()

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    wire = client.send()
    assert wire is not None
    server.receive(wire)

    # WT_CLOSE_SESSION を受理前に注入する
    close_payload = (0).to_bytes(4, "big")
    server.receive(_encode_data_frame(session_id, _encode_capsule(0x2843, close_payload)))

    # 受理前は SessionClosed が発火しない
    assert not [e for e in _drain_events(server) if e.type == h2.EventType.SESSION_CLOSED]

    # 受理で遅延処理され、SessionClosed が 1 回だけ発火する
    assert server.accept_session(session_id) is True
    closed_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1

    # 後続の送受信でも二重発火しない
    _h2_pump(server, client)
    _h2_pump(client, server)
    closed_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert closed_events == []


def test_pre_accept_buffer_overflow_keeps_connection() -> None:
    """413 拒否後も同一接続で後続セッションを確立できることを確認

    上限超過の拒否は接続を切らない。同一ペアで新規 CONNECT を送り、
    受理できることを検証する。
    """
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_pre_accept_buffer_limit = 10
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)

    # 上限超過で 413 拒否される
    rejected_id = client.connect("https://localhost/webtransport")
    assert rejected_id >= 0
    client.send_datagram(rejected_id, b"0123456789ABCDEF")
    wire = client.send()
    assert wire is not None
    server.receive(wire)
    _h2_pump(server, client)
    rejected = [e for e in _drain_events(client) if e.type == h2.EventType.SESSION_REJECTED]
    assert len(rejected) == 1
    assert rejected[0].status_code == 413

    # 同一接続で後続セッションを確立できる (先行セッションの READY は
    # 排水済みにする)
    _drain_events(server)
    session_id = client.connect("https://localhost/webtransport2")
    assert session_id >= 0
    _h2_pump(client, server)
    ready = [
        e
        for e in _drain_events(server)
        if e.type == h2.EventType.SESSION_READY and e.session_id == session_id
    ]
    assert len(ready) == 1
    assert server.accept_session(session_id) is True
    _h2_pump(server, client)
    assert [e for e in _drain_events(client) if e.type == h2.EventType.SESSION_READY]


def test_pre_accept_buffer_boundary_accepted() -> None:
    """蓄積量が上限ちょうどの場合は受理されることを確認"""
    client = h2.Session.create_client(h2.Config())
    server_config = h2.Config()
    server_config.is_server = True
    server_config.wt_pre_accept_buffer_limit = 10
    server = h2.Session.create_server(server_config)
    _h2_pump(client, server)
    _h2_pump(server, client)

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    # DATAGRAM カプセル全体がちょうど 10 バイトになるよう調整する
    # (Type 1 + Length 1 + 8 バイトペイロード)
    client.send_datagram(session_id, b"12345678")
    wire_parts = []
    while True:
        part = client.send()
        if part is None:
            break
        wire_parts.append(part)
    assert wire_parts
    server.receive(b"".join(wire_parts))

    # 上限ちょうどは拒否されず、受理後に配信される
    assert server.accept_session(session_id) is True
    datagram_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.DATAGRAM
    ]
    assert len(datagram_events) == 1
    assert datagram_events[0].data == b"12345678"


def test_pre_accept_error_capsule_skips_initial_credit() -> None:
    """蓄積中の不正カプセルで終了しても初期クレジットを送出しないことを確認

    受理前の蓄積に不正 WT_CLOSE_SESSION (メッセージ 1024 超) が混ざると、
    排出時にセッションエラーで終了する。終了済みセッションに初期
    WT_MAX_DATA / WT_MAX_STREAMS を送出しない。
    """
    client, server = _create_h2_session_pair()

    session_id = client.connect("https://localhost/webtransport")
    assert session_id >= 0
    wire_parts = []
    while True:
        part = client.send()
        if part is None:
            break
        wire_parts.append(part)
    assert wire_parts
    server.receive(b"".join(wire_parts))

    # 不正 WT_CLOSE_SESSION (メッセージ 1025 バイト) を受理前に注入する
    bad_payload = (0).to_bytes(4, "big") + b"x" * 1025
    server.receive(_encode_data_frame(session_id, _encode_capsule(0x2843, bad_payload)))

    # 受理で排出されると Error が発火し、初期クレジットは送出されない
    assert server.accept_session(session_id) is True
    error_events = [e for e in _drain_events(server) if e.type == h2.EventType.ERROR]
    assert len(error_events) == 1
    wire = server.send()
    assert wire is None or _encode_capsule(0x190B4D3D, _encode_varint(1048576)) not in wire
