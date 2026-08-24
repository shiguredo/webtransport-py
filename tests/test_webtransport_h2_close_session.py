"""WebTransport over HTTP/2 の close_session の送出抑止とエラーメッセージ切り詰めテスト

close_session の二重呼び出し送出抑止と、エラーメッセージの 1024 バイト
切り詰め (draft-15 Section 6.12 の UTF-8 文字境界後退) を検証する。
ローカル close_session の二重呼び出しで WT_CLOSE_SESSION capsule が 2 個
ワイヤへ送出されないことを検証する。close_session はエントリを残したまま
is_terminated を立てるため、修正前は 2 回目以降の呼び出しでも get_wt_session
が成功し、flush 前は WT_CLOSE_SESSION が 2 個ワイヤへ送出され、flush 後は
2 個目のカプセルが http2_stream_buffers_ に残留していた。残留は内部状態の
ため公開 API からは観測できない (stop_sending / drain_session のテスト
docstring も同旨) ため、本テストは送出の有無と個数で検証する。生存セッション
の 1 回の close_session は従来どおり送出される (回帰ピン)。
send_stream_data のフロー制御違反時 (FLOW_CONTROL_ERROR) の内部 close_session
呼び出しは is_terminated が立つ前のためガードで塞がれない (回帰ピン)。
WT_CLOSE_SESSION 受信後・ピアの END_STREAM 受信後・クライアントの非 2xx 拒否
受信後は close_session が修正前からエントリ不在 (get_wt_session の失敗) で
塞がれており、本変更の対象経路ではない (既存テストでカバー済み)。サーバー側
の reject_session の 2xx 送出後は修正で初めて is_terminated で塞がれる経路
だが、データプロバイダ未登録のため修正前後ともワイヤ送出されず、残留は内部
状態のため公開 API から観測できないことからテスト対象外。
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


def _encode_wt_close_session_capsule(error_code: int, error_message: str = "") -> bytes:
    """WT_CLOSE_SESSION capsule のワイヤバイト列を組み立てる

    Type 0x2843 は 2 バイト varint [0x68, 0x43] + Length + Application Error
    Code (32bit) + Message。Length は 1 バイト varint のみ対応する (テストで
    使う小さい値のみ。64 バイト未満のペイロード前提)。HTTP/2 DATA フレームの
    ペイロードはカプセルバイト列そのもののため、ワイヤデータに対する部分列
    チェックで送出を検証できる。
    """
    payload = error_code.to_bytes(4, "big") + error_message.encode("utf-8")
    assert len(payload) < 0x40, "Length が 1 バイト varint に収まる前提が崩れています"
    return b"\x68\x43" + bytes([len(payload)]) + payload


def _encode_wt_close_session_capsule_bytes(error_code: int, message: bytes) -> bytes:
    """1024 バイト超のメッセージでも扱える WT_CLOSE_SESSION capsule を組み立てる

    Length はテストで必要な 2 バイト varint の範囲を扱う (payload が 64
    バイト未満のときは 1 バイト varint になり、_encode_wt_close_session_capsule
    (文字列版) の出力とバイト列が一致する)。用意するカプセルは最大 1032
    バイトであり、既定の max_frame_size (16384 バイト) の単一 DATA フレームに
    収まるため、ワイヤ部分列チェックで送出を検証できる。
    """
    return (
        _encode_varint(0x2843)
        + _encode_varint(4 + len(message))
        + error_code.to_bytes(4, "big")
        + message
    )


def _create_h2_session_pair_with_server_stream_limit(
    limit: int,
) -> tuple[h2.Session, h2.Session]:
    """サーバー側の wt_initial_max_stream_data を縮小したペアを作成する

    クライアントのストリーム送信クレジットは対向 (サーバー) の SETTINGS 由来
    (draft-15 Section 4.3.1) のため、サーバー config の
    wt_initial_max_stream_data を小さな正の値にするとクライアントの送信
    クレジットが縮小する。0 にするとフォールバックで自側 config が使われ
    縮小しない (apply_peer_initial_flow_control の仕様)。クレジットは
    SETTINGS と 200 応答の WebTransport-Init ヘッダー (br) を max で合成する
    が (webtransport_h2.cpp の SETTINGS 受信・200 応答受信処理)、ヘルパーは
    config の値が両方に反映されるため 4 で一致し、合成経路で上書きされない。
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


def _encode_data_frame(session_id: int, payload: bytes = b"", end_stream: bool = False) -> bytes:
    """DATA フレームのワイヤバイト列を組み立てる

    END_STREAM フラグ (0x01) 付きで送出されたフレームを再現する。
    close_session の half-close (draft-15 Section 6.12 の送出側 MUST) の
    検証に使う。
    """
    flags = 0x01 if end_stream else 0x00
    return (
        len(payload).to_bytes(3, "big")
        + bytes([0x00, flags])
        + (session_id & 0x7FFFFFFF).to_bytes(4, "big")
        + payload
    )


def test_close_session_double_call_single_capsule() -> None:
    """close_session の二重呼び出しで WT_CLOSE_SESSION が 1 個だけ送出されることを確認

    ローカル close_session は flush のタイミングに依存せず is_terminated を
    立てる。修正前は 2 回目の呼び出しでも get_wt_session が成功して
    WT_CLOSE_SESSION capsule が再びキューされ、flush 前は 2 個がワイヤへ
    送出されていた (flush 後は 2 個目のカプセルが http2_stream_buffers_ に
    残留し、ストリームクローズ時まで保持された)。修正後は 2 回目が no-op に
    なり 1 個のみ送出される。2 回目の引数 (error code 42) がワイヤに現れない
    ことで「1 回目の呼び出しが優先され 2 回目は完全に無視される」ことを
    ピンする。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ローカル close_session を 2 回呼ぶ (flush はまだ)
    client.close_session(session_id, 0)
    client.close_session(session_id, 42, "second")
    wire = client.send()
    assert wire is not None
    assert wire.count(_encode_wt_close_session_capsule(0)) == 1
    assert _encode_wt_close_session_capsule(42, "second") not in wire

    # 1 回目の呼び出し由来の END_STREAM (half-close) はガードで失われない
    # (draft-15 Section 6.12 の送出側 MUST)
    assert (
        _encode_data_frame(session_id, _encode_wt_close_session_capsule(0), end_stream=True) in wire
    )


def test_close_session_after_flush_double_call_not_sent() -> None:
    """flush 後の close_session 再呼び出しが追加の送出を生まないことを確認

    1 回目の close_session で is_terminated が立つため、flush 完了後に 2 回目
    を呼んでも no-op になる。修正前は 2 個目のカプセルが http2_stream_buffers_
    に残留した (ストリームが half-closed (local) のためワイヤへは送出されず、
    残留は内部状態のため公開 API からは観測できない)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # 1 回目の close_session を flush まで完了させる
    client.close_session(session_id, 0)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_close_session_capsule(0) in wire

    # flush 後の再呼び出しは追加の送出を生まない
    client.close_session(session_id, 0)
    wire = client.send()
    assert wire is None or _encode_wt_close_session_capsule(0) not in wire


def test_close_session_alive_session_delivered() -> None:
    """生存セッションの 1 回の close_session は従来どおり送出されることを確認

    ガードは終了を学習したセッションにのみ適用され、生存セッションへの
    close_session はワイヤへ送出されてピアに SessionClosed イベントとして
    届く (回帰ピン)。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # クライアントの close_session がワイヤへ送出される
    client.close_session(session_id, 0)
    wire = client.send()
    assert wire is not None
    assert _encode_wt_close_session_capsule(0) in wire

    # ワイヤをサーバーに渡すと SessionClosed イベントになる
    # (send() は送信バッファ全体を返すため、取り出したワイヤをそのまま渡せる)
    server.receive(wire)
    closed_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].session_id == session_id
    assert closed_events[0].error_code == 0


def test_close_session_flow_control_violation_internal_call_delivered() -> None:
    """send_stream_data のフロー制御違反時の内部 close_session が送出されることを確認

    send_stream_data はフロー制御超過 (draft-15 Section 6.5 / 6.6) を検知
    すると FLOW_CONTROL_ERROR (0x50) で close_session を内部から呼ぶ。この
    呼び出しは is_terminated が立つ前に行われるため冒頭のガードで塞がれず、
    WT_CLOSE_SESSION がワイヤへ送出される (回帰ピン)。既に終了済みの場合は
    send_stream_data 冒頭のガードで内部呼び出し自体が発生しない。
    """
    client, server = _create_h2_session_pair_with_server_stream_limit(4)
    session_id = _connect_h2_session(client, server)
    stream_id = client.open_stream(session_id, False)
    assert stream_id >= 0

    # ストリーム送信クレジット 4 バイトを超えて送信するとフロー制御違反になる
    client.send_stream_data(session_id, stream_id, b"012345")
    wire = client.send()
    assert wire is not None
    assert _encode_wt_close_session_capsule(0x50, "flow control limit exceeded") in wire


def test_close_session_truncates_at_utf8_boundary() -> None:
    """1024 バイトを跨ぐマルチバイト文字が UTF-8 文字境界で切り詰められることを確認

    draft-15 Section 6.12 の MUST「Senders that truncate an application-supplied
    message MUST do so at a UTF-8 character boundary」に従い、1024 バイトで
    切り詰めると不完全な UTF-8 シーケンスが生じる場合は文字境界まで後退する。
    「あ」は 3 バイト (e3 81 82)。342 文字 (1026 バイト) のメッセージを
    1024 バイトで切ると 342 文字目の先頭 1 バイト (e3) が残るため、修正後は
    1 バイト後退して 341 文字 (1023 バイト) の「完全な UTF-8・1024 バイト
    以下」が送出される。部分列チェックで「不完全な 1024 バイトバージョン」が
    送出されないことも確認する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # 「あ」を 342 個並べた 1026 バイトのエラーメッセージで close_session を呼ぶ
    message = "あ" * 342
    assert len(message.encode("utf-8")) == 1026
    client.close_session(session_id, 1, message)
    wire = client.send()
    assert wire is not None

    # 文字境界で切り詰められた 1023 バイト版 (341 文字) が送信される
    expected_message = "あ" * 341
    truncated_capsule = _encode_wt_close_session_capsule_bytes(1, expected_message.encode("utf-8"))
    assert truncated_capsule in wire

    # バイト単位の切り詰めで生じた不完全な UTF-8 版は送出されない
    invalid_capsule = _encode_wt_close_session_capsule_bytes(1, message.encode("utf-8")[:1024])
    assert invalid_capsule not in wire

    # 送出されたカプセルをピアへ渡すと、正しいアプリケーションエラーコードで
    # SessionClosed として通知される (送信側トリミング後も意味論が壊れない)
    server.receive(wire)
    closed_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].error_code == 1
    assert len(closed_events[0].error_message.encode("utf-8")) == 1023
    assert closed_events[0].error_message == expected_message


def test_close_session_ascii_truncates_at_1024_bytes() -> None:
    """ASCII のみのメッセージは従来どおり 1024 バイトで切り詰められることを確認

    1 バイト文字のメッセージでは文字境界の調整が発生しないため、バイト単位の
    1024 バイト切り詰めがそのまま適用される (既存挙動のピン)。draft-15
    Section 6.12 の 1024 バイト制限はちょうど 1024 バイトまで合法。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    # ASCII 1026 バイト (1024 バイトを超える) のエラーメッセージで呼ぶ
    message = "a" * 1026
    assert len(message.encode("utf-8")) == 1026
    client.close_session(session_id, 1, message)
    wire = client.send()
    assert wire is not None

    # 1024 バイトで切り詰められたバージョンが送信される (1 バイト後の後退なし)
    expected_capsule = _encode_wt_close_session_capsule_bytes(1, b"a" * 1024)
    assert expected_capsule in wire

    # 完全な 1026 バイト版は送出されない
    full_capsule = _encode_wt_close_session_capsule_bytes(1, message.encode("utf-8"))
    assert full_capsule not in wire

    # ピア側はちょうど 1024 バイトを WT_ERROR にせず正常に受信する (受信側の
    # 上限判定は「超過」のみであり、1024 ちょうどは合法)
    server.receive(wire)
    closed_events = [
        event for event in _drain_events(server) if event.type == h2.EventType.SESSION_CLOSED
    ]
    assert len(closed_events) == 1
    assert closed_events[0].error_message == "a" * 1024


def test_close_session_truncates_at_4byte_char_boundary() -> None:
    """4 バイト文字の後退 (最大 3 回) が正しく行われることを確認

    「😀」(U+1F600、4 バイト: F0 9F 98 80) を 255 個 (1020 バイト) + 「A」
    (1 バイト) + 「😀」1 個 (計 1025 バイト) のメッセージを 1024
    バイトで切ると、末尾に 4 バイト文字の先頭 3 バイト (F0 9F 98) が残る。
    is_valid_utf8 は不完全シーケンスを拒否するため 3 バイト後退し、1021
    バイト (「😀」×255 + 「A」) が送出される。4 バイト文字の後退量が
    最大 (3 回) になるケースのピン。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    message = "😀" * 255 + "A" + "😀"
    assert len(message.encode("utf-8")) == 1025
    client.close_session(session_id, 1, message)
    wire = client.send()
    assert wire is not None

    # 3 バイト後退した 1021 バイト (「😀」×255 + 「A」) が送出される
    expected_capsule = _encode_wt_close_session_capsule_bytes(1, ("😀" * 255 + "A").encode("utf-8"))
    assert expected_capsule in wire


def test_close_session_exact_1024_bytes_no_truncation() -> None:
    """ちょうど 1024 バイトで文字境界が一致する場合は後退しないことを確認

    3 バイト文字 341 個 (1023 バイト) + ASCII 1 バイト (「b」) = ちょうど
    1024 バイト。バイト単位の切り詰めでは後退が必要ないため、完全な 1024
    バイトがそのまま送出される (draft-15 Section 6.12 では 1024 バイトちょうど
    は合法)。「有効な UTF-8 でも余計に後退してしまう」未来の回帰を検出する。
    """
    client, server = _create_h2_session_pair()
    session_id = _connect_h2_session(client, server)

    message = "あ" * 341 + "b"
    assert len(message.encode("utf-8")) == 1024
    client.close_session(session_id, 1, message)
    wire = client.send()
    assert wire is not None

    # 後退しない 1024 バイト版が送出される
    expected_capsule = _encode_wt_close_session_capsule_bytes(1, message.encode("utf-8"))
    assert expected_capsule in wire
