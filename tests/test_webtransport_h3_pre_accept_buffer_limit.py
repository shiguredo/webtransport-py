"""WebTransport over HTTP/3 の受理前ストリームのバッファ上限テスト

draft-ietf-webtrans-http3-16 Section 4.6 の MUST (受理前バッファの上限) を
満たすため、受理前 (セッション確定前) の WebTransport データストリームの
累計受信バイトが `h3.Config` の `wt_pre_accept_buffer_limit` を超えたら、
そのストリームを WT_BUFFERED_STREAM_REJECTED (0x3994BD84) で拒否すること
を検証する。上限以下では従来どおり受理後にアプリへ配信され、受理確定後の
ストリームは上限の対象外になる。
"""

from __future__ import annotations

from conftest import (
    _bind_session_streams,
    _drain_events,
    _encode_wt_stream_data,
    _pump,
)

from webtransport import h3

# WT_BUFFERED_STREAM_REJECTED (draft-ietf-webtrans-http3-16 Section 4.6)
_WT_BUFFERED_STREAM_REJECTED = 0x3994BD84


def _create_session_pair_with_limit(
    limit: int,
) -> tuple[h3.Session, h3.Session]:
    """受理前バッファ上限を設定した h3.Session のペアを作成する

    サーバー側のみ上限を設定し、クライアント側は既定値のままにする。
    """
    client = h3.Session.create_client(h3.Config())
    server_config = h3.Config()
    server_config.is_server = True
    server_config.wt_pre_accept_buffer_limit = limit
    server = h3.Session.create_server(server_config)
    _bind_session_streams(client, server)
    _pump(server, client)
    return client, server


def _connect_and_inject(
    limit: int,
    payloads: list[bytes],
) -> tuple[h3.Session, h3.Session, int]:
    """上限を設定したサーバーへ受理前にデータストリームを注入する

    1 つ目の payload は WT データストリームのワイヤ形式 (ストリームタイプ +
    セッション ID + ペイロード) として注入し、2 つ目以降は素のペイロードと
    して注入する。受理は行わない。

    @return (クライアント Session, サーバー Session, 注入した stream_id)
    """
    client, server = _create_session_pair_with_limit(limit)
    assert client.connect(0, "https://localhost/webtransport") is True
    # CONNECT を届ける (end_headers_cb で session_ids_ に入るが未受理)
    _pump(client, server)
    assert server.get_session_ids() == [0]

    # 受理前にピア起動双方向ストリーム (クライアント起動 %4==0) のデータを
    # 注入する
    stream_id = 4
    for index, payload in enumerate(payloads):
        if index == 0:
            data = _encode_wt_stream_data(0, payload)
        else:
            data = payload
        server.receive_stream_data(stream_id, data, False)
    return client, server, stream_id


def test_wt_pre_accept_buffer_limit_default_and_config() -> None:
    """h3.Config の既定値が 65536 で、設定できることを確認する"""
    assert h3.Config().wt_pre_accept_buffer_limit == 65536

    config = h3.Config()
    config.wt_pre_accept_buffer_limit = 128
    assert config.wt_pre_accept_buffer_limit == 128


def test_pre_accept_stream_rejected_over_limit() -> None:
    """上限を超えた受理前ストリームが WT_BUFFERED_STREAM_REJECTED で拒否されることを確認

    サーバーは CONNECT リクエスト受信後・受理前に届いたデータストリームを
    nghttp3 が WT_SESSION_BLOCKED でバッファリングする。累計が上限を超えた
    時点で STOP_SENDING イベントが発火し、以後のデータは受理されない。
    """
    client, server = _create_session_pair_with_limit(1024)

    assert client.connect(0, "https://localhost/webtransport") is True
    _pump(client, server)
    assert server.get_session_ids() == [0]

    # 1 回目 (600 バイト) は上限以下で拒否されない
    stream_id = 4
    payload = b"a" * 600
    server.receive_stream_data(stream_id, _encode_wt_stream_data(0, payload), False)
    assert all(e.type != h3.EventType.STOP_SENDING for e in _drain_events(server))

    # 2 回目で累計が上限 (1024 バイト) を超えるため拒否される
    server.receive_stream_data(stream_id, payload, False)
    stop_events = [e for e in _drain_events(server) if e.type == h3.EventType.STOP_SENDING]
    assert len(stop_events) == 1
    assert stop_events[0].stream_id == stream_id
    assert stop_events[0].error_code == _WT_BUFFERED_STREAM_REJECTED

    # 拒否後のデータは破棄され、戻り値は受け取ったバイト数になる
    assert server.receive_stream_data(stream_id, payload, False) == len(payload)

    # 受理後も拒否済みストリームのデータはアプリに配信されない
    assert server.accept_session(0) is True
    _pump(server, client)
    assert all(
        not (e.type == h3.EventType.STREAM_DATA and e.stream_id == stream_id)
        for e in _drain_events(server)
    )


def test_pre_accept_stream_boundary_exact_limit_not_rejected() -> None:
    """累計が上限ちょうどなら拒否せず、1 バイト超過で拒否することを確認する

    1 回目のペイロード 600 バイトはワイヤ形式のヘッダ 3 バイトを除いた
    600 バイトが計数され、上限 600 と等しいため拒否されない。2 回目の
    1 バイトで 601 バイトとなり拒否される。
    """
    _client, server, stream_id = _connect_and_inject(600, [b"a" * 600])
    assert all(e.type != h3.EventType.STOP_SENDING for e in _drain_events(server))

    server.receive_stream_data(stream_id, b"x", False)
    stop_events = [e for e in _drain_events(server) if e.type == h3.EventType.STOP_SENDING]
    assert len(stop_events) == 1
    assert stop_events[0].error_code == _WT_BUFFERED_STREAM_REJECTED


def test_pre_accept_stream_rejected_with_zero_limit() -> None:
    """上限 0 では受理前データを 1 バイトでも受信した時点で拒否されることを確認する"""
    _client, server, stream_id = _connect_and_inject(0, [b"x"])
    stop_events = [e for e in _drain_events(server) if e.type == h3.EventType.STOP_SENDING]
    assert len(stop_events) == 1
    assert stop_events[0].stream_id == stream_id
    assert stop_events[0].error_code == _WT_BUFFERED_STREAM_REJECTED


def test_pre_accept_stream_within_limit_delivered_after_accept() -> None:
    """上限以下の受理前ストリームが分割到着でも受理後に配信されることを確認

    上限による拒否が既存の受理前バッファリングを壊していないことを
    確認する (受理確定でバッファが解除され、分割されたデータが順に
    配信される)。
    """
    payload = b"pre-accept-payload"
    client, server, stream_id = _connect_and_inject(65536, [payload[:5], payload[5:]])
    assert all(e.type != h3.EventType.STOP_SENDING for e in _drain_events(server))

    # 受理するとバッファが解除され、データが配信される
    assert server.accept_session(0) is True
    _pump(server, client)
    delivered = b"".join(
        e.data
        for e in _drain_events(server)
        if e.type == h3.EventType.STREAM_DATA and e.stream_id == stream_id
    )
    assert delivered == payload


def test_accepted_stream_not_counted() -> None:
    """受理確定後のストリームが上限の対象外であることを確認する

    受理済みセッションのデータは受理前バッファではないため、上限を超えても
    拒否されず、アプリへ配信される。
    """
    client, server = _create_session_pair_with_limit(128)

    assert client.connect(0, "https://localhost/webtransport") is True
    _pump(client, server)
    assert server.accept_session(0) is True
    _pump(server, client)

    # 受理確定後に上限を超えるデータを注入する
    stream_id = 4
    payload = b"c" * 300
    server.receive_stream_data(stream_id, _encode_wt_stream_data(0, payload), False)
    events = _drain_events(server)
    assert all(e.type != h3.EventType.STOP_SENDING for e in events)
    delivered = b"".join(
        e.data for e in events if e.type == h3.EventType.STREAM_DATA and e.stream_id == stream_id
    )
    assert delivered == payload


def test_pre_accept_uni_stream_rejected_over_limit() -> None:
    """単方向 (0x54) の受理前ストリームでも上限で拒否されることを確認する

    双方向とは nghttp3 の受信経路が異なる (nghttp3_conn_read_wt_stream_uni)
    ため、単方向でも同じ計数・拒否が成立することを固定する。
    """
    client, server = _create_session_pair_with_limit(1024)
    assert client.connect(0, "https://localhost/webtransport") is True
    _pump(client, server)

    # クライアント起動単方向ストリーム (%4==2)。制御 (2) / QPACK (6, 10) は
    # バインド済みのため未使用の 14 を使う。単方向 WT データストリームの
    # ワイヤ形式はストリームタイプ 0x54 + セッション ID + ペイロード
    stream_id = 14
    payload = b"u" * 600
    server.receive_stream_data(stream_id, b"\x40\x54\x00" + payload, False)
    assert all(e.type != h3.EventType.STOP_SENDING for e in _drain_events(server))

    server.receive_stream_data(stream_id, payload, False)
    stop_events = [e for e in _drain_events(server) if e.type == h3.EventType.STOP_SENDING]
    assert len(stop_events) == 1
    assert stop_events[0].stream_id == stream_id
    assert stop_events[0].error_code == _WT_BUFFERED_STREAM_REJECTED


def test_rejected_stream_not_reinjected_after_local_reset() -> None:
    """拒否済みストリームをアプリ起点でリセットしても再投入されないことを確認

    アプリ起点の `reset_stream` は `close_stream` を経由して送信方向だけを
    閉じるため、ピアはデータを送り続け得る。拒否済み記録を解放しないことで
    nghttp3 のストリーム再生成 (ストリームタイプ誤解釈・H3_FRAME_UNEXPECTED)
    を防ぐ。
    """
    client, server = _create_session_pair_with_limit(1024)
    assert client.connect(0, "https://localhost/webtransport") is True
    _pump(client, server)

    # 上限超過で拒否する
    stream_id = 4
    payload = b"r" * 1200
    server.receive_stream_data(stream_id, _encode_wt_stream_data(0, payload), False)
    stop_events = [e for e in _drain_events(server) if e.type == h3.EventType.STOP_SENDING]
    assert len(stop_events) == 1

    # アプリ起点でリセットしても、その後のデータは破棄され、nghttp3 の
    # ストリーム再生成によるエラーは発生しない
    server.reset_stream(stream_id, 0)
    server.receive_stream_data(stream_id, b"\x00\x05hello", False)
    events = _drain_events(server)
    assert all(e.type != h3.EventType.ERROR for e in events)
    assert server.is_closed() is False


def test_pre_accept_stream_rejected_on_client() -> None:
    """クライアント側でも受理前ストリームが上限で拒否されることを確認

    クライアントは connect() 時点で session_ids_ に入るが、2xx 応答の
    受信までは受理確定ではない。2xx 前に届いたサーバー起動双方向
    ストリーム (%4==1) も同じ上限の対象になる。
    """
    client_config = h3.Config()
    client_config.wt_pre_accept_buffer_limit = 1024
    client = h3.Session.create_client(client_config)
    server = h3.Session.create_server(h3.Config())
    _bind_session_streams(client, server)
    _pump(server, client)

    assert client.connect(0, "https://localhost/webtransport") is True

    # 2xx 前 (受理確定前) にサーバー起動双方向ストリームのデータを注入する
    stream_id = 1
    payload = b"b" * 600
    client.receive_stream_data(stream_id, _encode_wt_stream_data(0, payload), False)
    assert all(e.type != h3.EventType.STOP_SENDING for e in _drain_events(client))

    client.receive_stream_data(stream_id, payload, False)
    stop_events = [e for e in _drain_events(client) if e.type == h3.EventType.STOP_SENDING]
    assert len(stop_events) == 1
    assert stop_events[0].stream_id == stream_id
    assert stop_events[0].error_code == _WT_BUFFERED_STREAM_REJECTED

    # 拒否後のデータは破棄され、戻り値は受け取ったバイト数になる
    assert client.receive_stream_data(stream_id, b"more", False) == len(b"more")
    assert all(e.type != h3.EventType.STREAM_DATA for e in _drain_events(client))
