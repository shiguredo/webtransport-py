"""WebTransport over HTTP/3 のストリーム・接続制御 API テスト"""

from conftest import _establish_session, _pump

from webtransport import h3


def test_block_unblock_stream() -> None:
    """ストリームのブロック / アンブロックで送信が止まることを確認"""
    client, server, session_id = _establish_session()

    # データストリームを開いてデータを送信する (block 前に flush して
    # しまうと unblock 後も再スケジュールされないため、flush しない)
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"hello")
    assert client.stream_writable(stream_id) == 1

    # block するとスケジューラから外れ、データが出ず書き込み不可になる
    client.block_stream(stream_id)
    assert client.stream_writable(stream_id) == 0
    assert client.get_streams_to_send() == []

    # unblock すると再スケジュールされ、データが出て書き込み可能になる
    assert client.unblock_stream(stream_id) is True
    assert client.stream_writable(stream_id) == 1

    # 取り出したデータをそのままピアに渡す (get_streams_to_send は
    # 取り出した時点で消費されるため)。データが空になるまで繰り返す
    sent_data = False
    for _ in range(64):
        streams = client.get_streams_to_send()
        if not streams:
            break
        for sid, data, fin in streams:
            if sid == stream_id:
                sent_data = True
            server.receive_stream_data(sid, data, fin)
    assert sent_data, "unblock 後にデータが再出しません"

    # ピアにデータが届く
    received = False
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == h3.EventType.STREAM_DATA:
            assert event.stream_id == stream_id
            assert event.data == b"hello"
            received = True
    assert received, "データが届きません"


def test_shutdown_stream_write_stops_sending() -> None:
    """書き込み側シャットダウン後は送信が止まり、send_stream_data が no-op になることを確認

    ピアの STOP_SENDING を受けたときに高レベル層が呼ぶ低レベル契約を検証する。
    シャットダウン前は積んだデータがピアへ届き、シャットダウン後は nghttp3 が
    書き込み不可 (SHUT_WR) になるため送出もされなくなる。加えて
    send_stream_data がバッファを積まない (no-op) ことも確認する。nghttp3 の
    SHUT_WR だけでも送出は止まるため、バッファの有無まで見ないと no-op を
    判別できない (no-op でなければ stream_buffers_ に積まれ、_has_stream_buffer
    が True になる)
    """
    client, server, session_id = _establish_session()

    # 前提: シャットダウン前はデータがピアへ届く
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"hello")
    _pump(client, server)

    received = False
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == h3.EventType.STREAM_DATA:
            assert event.stream_id == stream_id
            assert event.data == b"hello"
            received = True
    assert received, "シャットダウン前にデータが届きません"

    # 書き込み側をシャットダウンすると書き込み不可になる
    client.shutdown_stream_write(stream_id)
    assert client.stream_writable(stream_id) == 0

    # シャットダウン後の send_stream_data は no-op となり、送信バッファにも積まない
    client.send_stream_data(stream_id, b"after-stop-sending")
    assert client._has_stream_buffer(stream_id) is None, (
        "シャットダウン済みストリームへの send_stream_data がバッファを積んでいます"
    )

    # 積まれたデータは無いため、ピアには何も届かない
    _pump(client, server)
    assert server.next_event() is None, "シャットダウン後もデータが送出されています"


def test_shutdown_stream_write_blocks_pending_data() -> None:
    """書き込み側シャットダウン前に積んだデータが送出されないことを確認

    nghttp3 は SHUT_WR を立ててストリームをスケジューラから外すため、
    send_stream_data で積んだだけのデータはシャットダウン後に送出されない
    (RFC 9000 Section 3.5 は Ready / Send 状態での RESET_STREAM 送出を MUST と
    定めており、送出されたデータはピアに届く前に破棄される)
    """
    client, server, session_id = _establish_session()

    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"blocked-data")
    client.shutdown_stream_write(stream_id)

    _pump(client, server)
    assert server.next_event() is None, "シャットダウン後もデータが送出されています"


def test_shutdown_stream_write_unknown_stream_is_noop() -> None:
    """存在しないストリームの書き込み側シャットダウンが no-op であることを確認

    接続が無い・閉じている場合の no-op は、公開 API から conn_ を破棄する手段が
    無くモックも禁止のためテスト不能 (防御的ガードのみ)。範囲外のストリーム ID は
    nghttp3 の契約外であるためテストしない (http3 層の shutdown_stream_write と
    同じ契約)
    """
    client, _server, _session_id = _establish_session()

    # 存在しないストリームでも例外にならず、バッファも作らない
    client.shutdown_stream_write(999)
    assert client._has_stream_buffer(999) is None
    assert client.stream_writable(999) == 0


def test_max_concurrent_streams() -> None:
    """同時ストリーム数のヒントを設定してもセッション確立と送受信が継続できることを確認

    効果は外部から観測できない (現在値との max マージのため) ため、
    呼び出し後も通常の動作が続くことのみ確認する
    """
    client, server, session_id = _establish_session()

    # ヒントを設定する
    client.max_concurrent_streams(10)
    server.max_concurrent_streams(10)

    # ヒント設定後もデータストリームの送受信が継続できる
    stream_id = 4
    assert client.open_stream(session_id, stream_id, False) is True
    client.send_stream_data(stream_id, b"hello")
    _pump(client, server)

    received = False
    while True:
        event = server.next_event()
        if event is None:
            break
        if event.type == h3.EventType.STREAM_DATA:
            assert event.data == b"hello"
            received = True
    assert received, "データが届きません"


def test_block_unblock_guards() -> None:
    """存在しない・範囲外のストリームでは no-op / 成功になることを確認"""
    client, _server, _session_id = _establish_session()

    # 存在しないストリームの unblock_stream は成功扱い (nghttp3 が 0 を返す)
    assert client.unblock_stream(999) is True
    # 存在しないストリームの block_stream は no-op (例外なし)
    client.block_stream(999)
    # 負の値と NGHTTP3_MAX_VARINT (2**62 - 1) を超える値も nghttp3 が
    # assert なしで扱うため安全 (クラッシュしないことを確認する)
    assert client.unblock_stream(-1) is True
    client.block_stream(-1)
    assert client.unblock_stream(1 << 62) is True
    client.block_stream(1 << 62)

    # 「コネクションが無い場合の no-op / False」は公開 API から conn_ を
    # 破棄する手段が無くモックも禁止のためテスト不能 (防御的ガードのみ)
