"""WebTransport over HTTP/3 の WT_CLOSE_SESSION メッセージ送信トリミング・受信検証テスト

draft-ietf-webtrans-http3-16 Section 6 の MUST「Application Error Message が
1024 バイト超または不正な UTF-8 の場合、受信者は H3_MESSAGE_ERROR (0x010E)
でストリームをリセットする」と「Senders that truncate an application-supplied
message MUST do so at a UTF-8 character boundary」を検証する。送信側の
トリミングはワイヤ部分列チェック、受信側のリセットはイベント列で観測する。
"""

from __future__ import annotations

from conftest import (
    _create_session_pair,
    _drain_events,
    _encode_varint,
    _establish_session,
    _setup_connect,
)

from webtransport import h3

_H3_MESSAGE_ERROR = 0x010E


def _encode_capsule(capsule_type: int, payload: bytes) -> bytes:
    """Type / Length / Payload のカプセルバイト列を組み立てる"""
    return _encode_varint(capsule_type) + _encode_varint(len(payload)) + payload


def _encode_wt_close_session_capsule(error_code: int, message: bytes) -> bytes:
    """WT_CLOSE_SESSION capsule (Type 0x2843) のワイヤバイト列を組み立てる"""
    return _encode_capsule(0x2843, error_code.to_bytes(4, "big") + message)


def _encode_data_frame(payload: bytes) -> bytes:
    """DATA フレーム (Type 0x00, RFC 9114 Section 4.1) のワイヤバイト列を組み立てる

    HTTP/3 のストリームデータはフレーム化されて受信されるため、注入する
    WT_CLOSE_SESSION カプセルは DATA フレームとして包む (nghttp3 は
    フレームレイヤを自動パースする)。
    """
    return _encode_varint(0x00) + _encode_varint(len(payload)) + payload


def _inject_wt_close_session(session: h3.Session, stream_id: int, capsule: bytes) -> None:
    """WT_CLOSE_SESSION カプセルを DATA フレームとしてセッションへ注入する"""
    session.receive_stream_data(stream_id, _encode_data_frame(capsule), False)


def test_close_session_truncates_at_utf8_boundary() -> None:
    """1024 バイトを跨ぐマルチバイト文字が UTF-8 文字境界で切り詰められて送出されることを確認

    draft-16 Section 6 の MUST「Senders that truncate an application-supplied
    message MUST do so at a UTF-8 character boundary」に従い、バイト単位の
    1024 バイト切り詰めで不完全な UTF-8 シーケンスが生じる場合は文字境界まで
    後退する。「あ」(3 バイト) を 342 文字 (1026 バイト) 並べたメッセージは
    1024 バイトで切ると 342 文字目の先頭 1 バイト (e3) が残るため、1 バイト
    後退して 341 文字 (1023 バイト) が送出される。修正前は nghttp3 が
    1024 バイト超を NGHTTP3_ERR_INVALID_ARGUMENT で拒否し、close_session が
    黙って失敗していた。
    """
    _client, server, session_id = _establish_session()

    message = "あ" * 342
    assert len(message.encode("utf-8")) == 1026
    server.close_session(session_id, 1, message)
    streams = server.get_streams_to_send()

    # 文字境界で切り詰められた 1023 バイト版 (341 文字) が CONNECT ストリーム
    # として書き出される
    expected_capsule = _encode_wt_close_session_capsule(1, ("あ" * 341).encode("utf-8"))
    assert any(
        stream_id == session_id and expected_capsule in data for stream_id, data, fin in streams
    ), "UTF-8 境界で切り詰められた WT_CLOSE_SESSION が送出されていません"


def test_close_session_ascii_truncates_at_1024_bytes() -> None:
    """ASCII のみのメッセージは従来どおり 1024 バイトで切り詰められることを確認

    1 バイト文字では文字境界の調整が発生しないため、バイト単位の 1024 バイト
    切り詰めがそのまま適用される。draft-16 Section 6 の 1024 バイト制限は
    ちょうど 1024 バイトまで合法。
    """
    _client, server, session_id = _establish_session()

    message = "a" * 1026
    assert len(message.encode("utf-8")) == 1026
    server.close_session(session_id, 1, message)
    streams = server.get_streams_to_send()

    expected_capsule = _encode_wt_close_session_capsule(1, b"a" * 1024)
    assert any(
        stream_id == session_id and expected_capsule in data for stream_id, data, fin in streams
    ), "1024 バイト切り詰めの WT_CLOSE_SESSION が送出されていません"


def test_client_receive_over_1024_bytes_resets_with_h3_message_error() -> None:
    """1024 バイト超過の Application Error Message で H3_MESSAGE_ERROR リセットが発生することを確認

    draft-16 Section 6 の MUST「If the Application Error Message exceeds 1024
    bytes ... the receiver MUST reset the stream with code H3_MESSAGE_ERROR」。
    1024 バイト超過は nghttp3 が LENGTH 段階で NGHTTP3_ERR_H3_MESSAGE_ERROR
    を返しコールバックは発火しないため、receive_stream_data の負値分岐から
    CONNECT ストリームが 0x010E でリセットされる。ResetStream イベント
    (QUIC RESET_STREAM の送出要求) と SessionClosed が積まれ、セッション ID は
    削除されることを検証する。
    """
    client, _server, session_id = _establish_session()

    # 1024 バイトを超えるペイロード (error code 4 バイト + 1025 バイト)
    capsule = _encode_wt_close_session_capsule(0, b"\x00" * 1025)
    _inject_wt_close_session(client, session_id, capsule)

    # セッション ID は削除され、リセット要求とセッション終了が観測される
    assert client.get_session_ids() == []
    events = _drain_events(client)
    reset_events = [
        event
        for event in events
        if event.type == h3.EventType.RESET_STREAM and event.error_code == _H3_MESSAGE_ERROR
    ]
    assert len(reset_events) == 1
    assert reset_events[0].stream_id == session_id
    assert any(event.type == h3.EventType.SESSION_CLOSED for event in events)
    # ストリームエラーは接続エラーではないため、セッションは閉じない
    assert client.is_closed() is False


def test_client_receive_invalid_utf8_resets_with_h3_message_error() -> None:
    """不正な UTF-8 の Application Error Message で H3_MESSAGE_ERROR リセットが発生することを確認

    draft-16 Section 6 の MUST「is not valid UTF-8 ... the receiver MUST reset
    the stream with code H3_MESSAGE_ERROR」。不正 UTF-8 は recv_wt_close_session_cb
    内で検知し (コールバックの非 0 戻りが NGHTTP3_ERR_CALLBACK_FAILURE として
    返る)、保留したセッション ID のリセット処理が receive_stream_data の
    負値分岐で実行される。接続エラー通知 (Error イベント) は既存どおり積まれる。
    """
    client, _server, session_id = _establish_session()

    # 不正な UTF-8 (1024 バイト以下) を含む WT_CLOSE_SESSION を注入する
    capsule = _encode_wt_close_session_capsule(0, b"\xff\xfe\x80\xbf")
    _inject_wt_close_session(client, session_id, capsule)

    assert client.get_session_ids() == []
    events = _drain_events(client)
    reset_events = [
        event
        for event in events
        if event.type == h3.EventType.RESET_STREAM and event.error_code == _H3_MESSAGE_ERROR
    ]
    assert len(reset_events) == 1
    assert reset_events[0].stream_id == session_id
    assert any(event.type == h3.EventType.SESSION_CLOSED for event in events)
    # ストリームエラーは接続エラーではないため、セッションは閉じない
    # (接続エラーのみ closed_ にすること)
    assert client.is_closed() is False


def test_server_accept_pre_buffer_over_1024_bytes_resets_with_h3_message_error() -> None:
    """accept_session 前にバッファされた 1024 バイト超の WT_CLOSE_SESSION でリセットされることを確認

    受理前にバッファされた WT_CLOSE_SESSION カプセルは confirm (accept_session)
    の処理中 (process_blocked_wt_stream_data) に処理される (draft-16
    Section 3.2: サーバーは 2xx を送るまでカプセルを処理しない)。1024 バイト
    超の場合、コールバックは発火せず nghttp3 が NGHTTP3_ERR_H3_MESSAGE_ERROR
    を返して confirm が失敗するため、accept_session は False を返す。draft-16
    Section 6 の MUST に従い、失敗分岐で CONNECT ストリームの 0x010E リセット
    が実行され、セッション ID が削除されることを検証する。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)

    # CONNECT リクエストヘッダーに続けて WT_CLOSE_SESSION (DATA フレーム付き)
    # を同一ストリームで送る。サーバーは 2xx 応答前のためカプセルはバッファ
    # され、confirm 時に処理される
    capsule = _encode_wt_close_session_capsule(0, b"\x00" * 1025)
    server.receive_stream_data(0, headers + _encode_data_frame(capsule), False)
    assert server.get_session_ids() == [0]

    # confirm が失敗し、リセット処理が実行される
    assert server.accept_session(0) is False
    assert server.get_session_ids() == []
    events = _drain_events(server)
    reset_events = [
        event
        for event in events
        if event.type == h3.EventType.RESET_STREAM and event.error_code == _H3_MESSAGE_ERROR
    ]
    assert len(reset_events) == 1
    assert reset_events[0].stream_id == 0
    assert any(event.type == h3.EventType.SESSION_CLOSED for event in events)
    # ストリームエラーは接続エラーではないため、セッションは閉じない
    assert server.is_closed() is False


def test_server_accept_pre_buffer_invalid_utf8_resets_with_h3_message_error() -> None:
    """accept_session 前にバッファされた不正 UTF-8 の WT_CLOSE_SESSION でリセットされることを確認

    受理前にバッファされた WT_CLOSE_SESSION カプセルは confirm (accept_session)
    の処理中に処理される。不正 UTF-8 は recv_wt_close_session_cb 内で検知されて
    保留され、コールバックの非 0 戻りが NGHTTP3_ERR_CALLBACK_FAILURE として
    confirm の失敗に合流する (1024 バイト超の経路は confirm 自体が
    NGHTTP3_ERR_H3_MESSAGE_ERROR で失敗するため、判定機構が異なる)。draft-16
    Section 6 の MUST に従い、失敗分岐で CONNECT ストリームの 0x010E リセット
    が実行され、セッション ID が削除されることを検証する。
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    headers = _setup_connect(client, server, 0)

    # CONNECT リクエストヘッダーに続けて不正 UTF-8 の WT_CLOSE_SESSION
    # (DATA フレーム付き) を同一ストリームで送る。サーバーは 2xx 応答前の
    # ためカプセルはバッファされ、confirm 時に処理される
    capsule = _encode_wt_close_session_capsule(0, b"\xff\xfe\x80\xbf")
    server.receive_stream_data(0, headers + _encode_data_frame(capsule), False)
    assert server.get_session_ids() == [0]

    # confirm が失敗し、リセット処理が実行される
    assert server.accept_session(0) is False
    assert server.get_session_ids() == []
    events = _drain_events(server)
    reset_events = [
        event
        for event in events
        if event.type == h3.EventType.RESET_STREAM and event.error_code == _H3_MESSAGE_ERROR
    ]
    assert len(reset_events) == 1
    assert reset_events[0].stream_id == 0
    assert any(event.type == h3.EventType.SESSION_CLOSED for event in events)
    # ストリームエラーは接続エラーではないため、セッションは閉じない
    assert server.is_closed() is False


def test_client_receive_exact_1024_bytes_message_accepted() -> None:
    """ちょうど 1024 バイトの Application Error Message は正常に受理されることを確認

    draft-16 Section 6 の「its length MUST NOT exceed 1024 bytes」は超過のみを
    禁じる。1024 バイトちょうどは合法であり、0x010E リセットは発生せず
    SessionClosed が正常に届く。
    """
    client, _server, session_id = _establish_session()

    capsule = _encode_wt_close_session_capsule(0, b"a" * 1024)
    _inject_wt_close_session(client, session_id, capsule)

    assert client.get_session_ids() == []
    events = _drain_events(client)
    # 正常終了でも nghttp3 は WT_SESSION_GONE (0x170D7B68) の RESET_STREAM /
    # STOP_SENDING を発火するため、0x010E が使われないことのみ検証する
    assert all(event.error_code != _H3_MESSAGE_ERROR for event in events), (
        "1024 バイトちょうどのカプセルで H3_MESSAGE_ERROR リセットが発生しました"
    )
    closed_events = [event for event in events if event.type == h3.EventType.SESSION_CLOSED]
    assert len(closed_events) == 1
    assert closed_events[0].error_message == "a" * 1024


def test_client_receive_empty_message_normal_close() -> None:
    """エラーメッセージなし (error code のみ) の WT_CLOSE_SESSION は正常に閉じることを確認

    カプセルの Length が 4 (error code のみ) は nghttp3 の LENGTH 段階チェック
    (4 バイト未満のみ不正) を通過する合法な形式であり、0x010E リセットは
    発生せず SessionClosed が届く。
    """
    client, _server, session_id = _establish_session()

    capsule = _encode_wt_close_session_capsule(0, b"")
    _inject_wt_close_session(client, session_id, capsule)

    assert client.get_session_ids() == []
    events = _drain_events(client)
    # 正常終了でも nghttp3 は WT_SESSION_GONE (0x170D7B68) の RESET_STREAM /
    # STOP_SENDING を発火するため、0x010E が使われないことのみ検証する
    assert all(event.error_code != _H3_MESSAGE_ERROR for event in events), (
        "空メッセージのカプセルで H3_MESSAGE_ERROR リセットが発生しました"
    )
    assert any(event.type == h3.EventType.SESSION_CLOSED for event in events)


def test_client_receive_too_short_length_resets_with_h3_message_error() -> None:
    """4 バイト未満の不正な長さのカプセルで H3_MESSAGE_ERROR リセットが発生することを確認

    Application Error Code (32 bit) はカプセルの必須要素であるため、Length が
    4 バイト未満の WT_CLOSE_SESSION は nghttp3 の LENGTH 段階チェックが
    NGHTTP3_ERR_H3_MESSAGE_ERROR を返し、1024 バイト超と同様に 0x010E リセット
    が実行される。ここでは payload 全体を 3 バイトにする。
    """
    client, _server, session_id = _establish_session()

    # 不正な長さ (payload 3 バイト) のカプセルを DATA フレームで注入する
    capsule = _encode_capsule(0x2843, b"\x00\x00\x00")
    client.receive_stream_data(session_id, _encode_data_frame(capsule), False)

    assert client.get_session_ids() == []
    events = _drain_events(client)
    reset_events = [
        event
        for event in events
        if event.type == h3.EventType.RESET_STREAM and event.error_code == _H3_MESSAGE_ERROR
    ]
    assert len(reset_events) == 1
    assert reset_events[0].stream_id == session_id


def test_client_receive_1024_bytes_invalid_utf8_resets_with_h3_message_error() -> None:
    """1024 バイト全体が不正な UTF-8 の Application Error Message でリセットされることを確認

    メッセージ長がちょうど 1024 バイトでも、内容が不正な UTF-8 (0xFF 連続)
    であれば recv_wt_close_session_cb の検知で H3_MESSAGE_ERROR リセットが
    発生する (draft-16 Section 6 の MUST)。msglen = 1024 はコールバック経路の
    最大長である。
    """
    client, _server, session_id = _establish_session()

    capsule = _encode_wt_close_session_capsule(0, b"\xff" * 1024)
    _inject_wt_close_session(client, session_id, capsule)

    assert client.get_session_ids() == []
    events = _drain_events(client)
    reset_events = [
        event
        for event in events
        if event.type == h3.EventType.RESET_STREAM and event.error_code == _H3_MESSAGE_ERROR
    ]
    assert len(reset_events) == 1
    assert reset_events[0].stream_id == session_id


def test_connection_error_closes_session() -> None:
    """nghttp3 の接続エラーで H3Session が closed_ になることを確認

    サーバーが自身が開始したストリーム ID (パリティ違反: % 4 == 1 を
    receive) を受信すると、nghttp3 は NGHTTP3_ERR_H3_STREAM_CREATION_ERROR
    (-609) を返す (read_stream2 の API 契約では「負値 = 接続エラーであり、
    接続を閉じなければならない」)。H3Session はこの負値を検知して
    closed_ = true にし、高レベル Client.run / Server.run が is_closed()
    で終了できるようにする (接続エラー時 run() が終了しないハングの
    修正。ストリームレベルのエラー = WT_CLOSE_SESSION の不正メッセージ
    リセット処理は接続を継続する)。
    """
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)

    # パリティ違反 (サーバー開始双方向 = % 4 == 1) のストリームを受信する
    ret = server.receive_stream_data(1, b"\x00", False)
    assert ret == 0
    assert server.is_closed() is True
