"""WebTransport over HTTP/3 の SETTINGS 受信判定テスト

Client.connect は SETTINGS 受信完了を「制御ストリーム (stream_id=3) の
データ受信」で判定していたが、RFC 9114 Section 6.2.1 は制御ストリームの ID を
固定せず、サーバーが QPACK エンコーダーを先に開けば stream_id 3 は QPACK
エンコーダーになる。H3Session.is_webtransport_ready() が SETTINGS の
wt_enabled / enable_connect_protocol / h3_datagram を直接判定することを
検証する (draft-ietf-webtrans-http3-16 Section 3.1)。
"""

from __future__ import annotations

from conftest import _bind_session_streams, _drain_events, _pump

from webtransport import h3


def _create_pair() -> tuple[h3.Session, h3.Session]:
    """ストリームをバインドした直後 (SETTINGS 未送信) の h3.Session ペアを作成する

    サーバーの SETTINGS はまだクライアントへ送らない。
    """
    client = h3.Session.create_client(h3.Config())
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)
    _bind_session_streams(client, server)
    return client, server


def _create_pair_with_encoder_first() -> tuple[h3.Session, h3.Session]:
    """サーバーが QPACK エンコーダーを制御ストリームより先に開いたペアを作成する

    QUIC の単方向ストリーム ID は開設順に割り当てられる (RFC 9000 Section
    2.1) ため、エンコーダーを先に開くと stream_id 3 が QPACK エンコーダー、
    制御ストリームが 11 (エンコーダー 3 / デコーダー 7) になる。
    """
    client = h3.Session.create_client(h3.Config())
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)
    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_qpack_encoder_stream(3)
    server.bind_qpack_decoder_stream(7)
    server.bind_control_stream(11)
    server.set_max_client_streams_bidi(100)
    return client, server


def _split_server_streams(
    server: h3.Session, control_stream_id: int, encoder_stream_id: int
) -> tuple[bytes, bytes]:
    """サーバーの送信データを制御ストリームと QPACK エンコーダーに分けて保持する

    get_streams_to_send は 1 回で全てのデータを返すとは限らないため、
    データが無くなるまで繰り返す (無限ループ防止のため最大 64 回)。

    @return (制御ストリームのデータ, エンコーダーストリームのデータ)
    """
    control_data = b""
    encoder_data = b""
    for _ in range(64):
        streams = server.get_streams_to_send()
        if not streams:
            break
        for stream_id, data, _fin in streams:
            if stream_id == control_stream_id:
                control_data += data
            elif stream_id == encoder_stream_id:
                encoder_data += data
    else:
        raise AssertionError("サーバーの送信データが 64 回で尽きませんでした")
    return control_data, encoder_data


def test_webtransport_ready_false_before_settings() -> None:
    """SETTINGS 未受信では is_webtransport_ready() が偽であることを確認"""
    client, _server = _create_pair()
    assert client.is_webtransport_ready() is False


def test_webtransport_ready_false_with_control_stream_type_only() -> None:
    """制御ストリームのタイプバイトのみでは SETTINGS 未受信として偽のままであることを確認

    制御ストリームにデータが届いたことだけでは SETTINGS の受信完了を意味
    しない (タイプバイトのみで真にしていたのが旧実装の不具合)。
    """
    client, _server = _create_pair()

    # 制御ストリームのストリームタイプ (0x00) のみを届ける
    client.receive_stream_data(3, b"\x00", False)
    assert client.is_webtransport_ready() is False


def test_webtransport_ready_false_with_partial_settings() -> None:
    """SETTINGS を受信しても 3 設定が揃わなければ偽であることを確認

    制御ストリームタイプ (0x00) + SETTINGS フレーム (0x04) に H3_DATAGRAM
    (0x33) のみを載せた場合、SETTINGS 受信済みでも wt_enabled /
    enable_connect_protocol が無いため偽になる (3 設定の論理積)。
    """
    client, _server = _create_pair()

    # 制御ストリームタイプ (0x00) + SETTINGS フレーム (0x04) + Length (0x02) +
    # H3_DATAGRAM (0x33) = 1。フレームが nghttp3 に受理された (5 バイト消費
    # され、Error イベントが積まれない) ことを確認してから判定する
    assert client.receive_stream_data(3, b"\x00\x04\x02\x33\x01", False) == 5
    assert all(event.type != h3.EventType.ERROR for event in _drain_events(client))
    assert client.is_webtransport_ready() is False


def test_webtransport_ready_after_encoder_stream_opened_first() -> None:
    """QPACK エンコーダーが stream_id 3 でも SETTINGS 受信で真になることを確認

    サーバーが先に開いた QPACK エンコーダーが stream_id 3、制御ストリームが
    11 の順序でも、エンコーダー (3) の到着では偽のまま、制御ストリーム (11)
    の SETTINGS 到着で真になる (制御ストリーム ID に依存しない)。
    """
    client, server = _create_pair_with_encoder_first()
    control_data, encoder_data = _split_server_streams(server, 11, 3)

    # エンコーダーストリーム (3) のタイプバイトが先に届いても偽のまま
    assert encoder_data, "サーバーの QPACK エンコーダーデータがありません"
    client.receive_stream_data(3, encoder_data, False)
    assert client.is_webtransport_ready() is False

    # 制御ストリーム (11) の SETTINGS が届いたら真になる
    assert control_data, "サーバーの制御ストリームデータがありません"
    client.receive_stream_data(11, control_data, False)
    assert client.is_webtransport_ready() is True


def test_webtransport_ready_false_until_settings_frame_complete() -> None:
    """SETTINGS フレームが分割受信された場合、完了までは偽のままであることを確認

    制御ストリームの SETTINGS を 2 回に分けて届け、途中では偽、最終バイトの
    投入で真になる (フレーム完了時にのみ recv_settings2_cb が発火する)。
    """
    client, server = _create_pair()
    control_data, _encoder_data = _split_server_streams(server, 3, 7)
    assert len(control_data) > 1
    split = len(control_data) // 2

    client.receive_stream_data(3, control_data[:split], False)
    assert client.is_webtransport_ready() is False

    client.receive_stream_data(3, control_data[split:], False)
    assert client.is_webtransport_ready() is True


def test_webtransport_ready_true_after_full_settings_exchange() -> None:
    """サーバーの SETTINGS を通常の交換で受信すると真になることを確認"""
    client, server = _create_pair()
    _pump(server, client)
    assert client.is_webtransport_ready() is True
