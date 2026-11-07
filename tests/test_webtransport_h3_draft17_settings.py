"""WebTransport over HTTP/3 の draft-17 SETTINGS_WT_ENABLED 互換テスト

draft-ietf-webtrans-http3-17 Section 9.2 は SETTINGS_WT_ENABLED の codepoint を
0x20b8933b に変更した。nghttp3 の webtransport ブランチは draft-16 の
0x2c7cf000 しか送出せず、受信側でも未知の SETTINGS ID を破棄するため、
バインディング層が次の 2 点を吸収する。

- 送出: 制御ストリームの SETTINGS に draft-16 の codepoint と draft-17 の
  codepoint を併送する (Section 7.1 のバージョン交渉)
- 受信: 制御ストリームの SETTINGS の draft-17 の codepoint を draft-16 の
  codepoint に読み替えて nghttp3 へ渡し、WebTransport 対応の判定に使う

本テストは実際の h3.Session を使って、SETTINGS の送出内容と受信判定を検証する。
"""

from conftest import _bind_session_streams

from webtransport import h3

# draft-ietf-webtrans-http3-16 の SETTINGS_WT_ENABLED
_WT_ENABLED_DRAFT16 = 0x2C7CF000
# draft-ietf-webtrans-http3-17 の SETTINGS_WT_ENABLED
_WT_ENABLED_DRAFT17 = 0x20B8933B
# draft-ietf-webtrans-http3-02 の SETTINGS_ENABLE_WEBTRANSPORT
_WT_ENABLED_ANCIENT_DRAFT2 = 0x2B603742
# SETTINGS_ENABLE_CONNECT_PROTOCOL
_ENABLE_CONNECT_PROTOCOL = 0x08
# SETTINGS_H3_DATAGRAM
_H3_DATAGRAM = 0x33


def _encode_varint(value: int) -> bytes:
    """RFC 9000 Section 16 の可変長整数をエンコードする"""
    if value < 0x40:
        return bytes([value])
    if value < 0x4000:
        return (value | 0x4000).to_bytes(2, "big")
    if value < 0x40000000:
        return (value | 0x80000000).to_bytes(4, "big")
    return (value | 0xC000000000000000).to_bytes(8, "big")


def _decode_varint(data: bytes, offset: int) -> tuple[int, int]:
    """RFC 9000 Section 16 の可変長整数をデコードする

    @return (値, 次のオフセット)
    """
    prefix = data[offset] >> 6
    length = 1 << prefix
    value = data[offset] & 0x3F
    for index in range(1, length):
        value = (value << 8) | data[offset + index]
    return value, offset + length


def _parse_settings(data: bytes) -> list[tuple[int, int]]:
    """制御ストリームの先頭にある SETTINGS フレームを (ID, 値) の列にする

    @param data 制御ストリームタイプ (0x00) から始まるバイト列
    """
    stream_type, offset = _decode_varint(data, 0)
    assert stream_type == 0x00, "制御ストリームタイプが 0x00 ではありません"
    frame_type, offset = _decode_varint(data, offset)
    assert frame_type == 0x04, "先頭フレームが SETTINGS ではありません"
    payload_length, offset = _decode_varint(data, offset)
    payload_end = offset + payload_length
    assert payload_end == len(data), "SETTINGS の長さが受信データと一致しません"

    entries: list[tuple[int, int]] = []
    while offset < payload_end:
        setting_id, offset = _decode_varint(data, offset)
        value, offset = _decode_varint(data, offset)
        entries.append((setting_id, value))
    assert offset == payload_end, "SETTINGS の解釈位置が末尾と一致しません"
    return entries


def _make_settings_frame(entries: list[tuple[int, int]]) -> bytes:
    """制御ストリームタイプ + SETTINGS フレームを組み立てる"""
    payload = b"".join(
        _encode_varint(setting_id) + _encode_varint(value) for setting_id, value in entries
    )
    return b"\x00\x04" + _encode_varint(len(payload)) + payload


def _create_pair() -> tuple[h3.Session, h3.Session]:
    """制御 / QPACK ストリームをバインドしたクライアント・サーバーペアを作る"""
    client = h3.Session.create_client(h3.Config())
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)
    _bind_session_streams(client, server)
    return client, server


def _collect_control_data(session: h3.Session, control_stream_id: int) -> bytes:
    """制御ストリームへ送出するデータをすべて集める"""
    data = b""
    for _ in range(16):
        sent = False
        for stream_id, chunk, _fin in session.get_streams_to_send():
            if stream_id == control_stream_id:
                data += chunk
                sent = True
        if not sent:
            break
    assert data, "制御ストリームのデータがありません"
    return data


def _minimal_draft17_settings() -> list[tuple[int, int]]:
    """draft-17 の codepoint だけを持つ最小の SETTINGS を返す

    draft-16 やそれ以前の互換用 codepoint を含めない、draft-17 のみに
    対応するピアを模擬する。
    """
    return [
        (_ENABLE_CONNECT_PROTOCOL, 1),
        (_H3_DATAGRAM, 1),
        (_WT_ENABLED_DRAFT17, 1),
    ]


def test_client_sends_draft17_and_draft16_wt_enabled() -> None:
    """クライアントが draft-16 と draft-17 の codepoint を併送することを確認"""
    client, _server = _create_pair()
    entries = dict(_parse_settings(_collect_control_data(client, 2)))

    assert entries.get(_WT_ENABLED_DRAFT17) == 1, (
        "draft-17 の SETTINGS_WT_ENABLED (0x20b8933b) が送出されていません"
    )
    assert entries.get(_WT_ENABLED_DRAFT16) == 1, (
        "draft-16 の SETTINGS_WT_ENABLED (0x2c7cf000) が送出されていません"
    )


def test_server_sends_draft17_and_draft16_wt_enabled() -> None:
    """サーバーが draft-16 と draft-17 の codepoint を併送することを確認"""
    _client, server = _create_pair()
    entries = dict(_parse_settings(_collect_control_data(server, 3)))

    assert entries.get(_WT_ENABLED_DRAFT17) == 1, (
        "draft-17 の SETTINGS_WT_ENABLED (0x20b8933b) が送出されていません"
    )
    assert entries.get(_WT_ENABLED_DRAFT16) == 1, (
        "draft-16 の SETTINGS_WT_ENABLED (0x2c7cf000) が送出されていません"
    )


def test_ancient_compat_codepoint_is_replaced_by_draft17() -> None:
    """互換用の古い codepoint を draft-17 に置き換えていることを確認

    SETTINGS フレームの長さを変えないため、もっとも古い draft-02 の
    codepoint を置き換える。draft-16 と draft-13/14 の codepoint は残す。
    """
    client, _server = _create_pair()
    entries = dict(_parse_settings(_collect_control_data(client, 2)))

    assert entries.get(_WT_ENABLED_ANCIENT_DRAFT2) is None, (
        "置き換え対象の古い codepoint が残っています"
    )
    assert entries.get(_WT_ENABLED_DRAFT17) == 1


def test_client_ready_with_draft17_only_server_settings() -> None:
    """draft-17 の codepoint だけを送るサーバーでも WebTransport 対応と判定する"""
    client, _server = _create_pair()
    frame = _make_settings_frame(_minimal_draft17_settings())

    consumed = client.receive_stream_data(3, frame, False)

    assert consumed == len(frame)
    assert client.is_webtransport_ready() is True, (
        "draft-17 の SETTINGS_WT_ENABLED だけでは WebTransport 対応と判定されていません"
    )


def test_client_can_connect_after_draft17_only_server_settings() -> None:
    """draft-17 のみのサーバー SETTINGS でも CONNECT を送信できることを確認"""
    client, _server = _create_pair()
    frame = _make_settings_frame(_minimal_draft17_settings())

    client.receive_stream_data(3, frame, False)

    assert client.is_webtransport_ready() is True
    assert client.connect(0, "https://localhost/webtransport") is True, (
        "draft-17 のみのサーバー SETTINGS では CONNECT を送信できません"
    )


def test_client_ready_with_draft16_only_server_settings() -> None:
    """draft-16 の codepoint だけを送るサーバーとも接続できることを確認"""
    client, _server = _create_pair()
    frame = _make_settings_frame(
        [
            (_ENABLE_CONNECT_PROTOCOL, 1),
            (_H3_DATAGRAM, 1),
            (_WT_ENABLED_DRAFT16, 1),
        ]
    )

    client.receive_stream_data(3, frame, False)

    assert client.is_webtransport_ready() is True, (
        "draft-16 の SETTINGS_WT_ENABLED だけでは WebTransport 対応と判定されていません"
    )
    assert client.connect(0, "https://localhost/webtransport") is True, (
        "draft-16 のみのサーバー SETTINGS では CONNECT を送信できません"
    )


def test_client_detects_draft17_setting_split_at_every_byte() -> None:
    """SETTINGS が 1 バイトずつ分割到着しても draft-17 の codepoint を検出する"""
    _client, _server = _create_pair()
    frame = _make_settings_frame(_minimal_draft17_settings())

    for split in range(1, len(frame)):
        client, _server = _create_pair()
        client.receive_stream_data(3, frame[:split], False)
        assert client.is_webtransport_ready() is False, (
            f"{split} バイト目までの到着ではまだ判定してはいけません"
        )
        client.receive_stream_data(3, frame[split:], False)
        assert client.is_webtransport_ready() is True, (
            f"{split} バイト目で分割すると draft-17 の codepoint を検出できません"
        )


def test_client_does_not_detect_draft17_setting_on_non_control_stream() -> None:
    """非制御の単方向ストリームに draft-17 の codepoint があっても判定しない"""
    client, _server = _create_pair()

    # QPACK エンコーダーストリームタイプ (0x02) の後に、偶然 codepoint と
    # 同じバイト列を置く。制御ストリームではないため観測対象外である
    payload = (
        b"\x02" + _encode_varint(0x01) + _encode_varint(_WT_ENABLED_DRAFT17) + _encode_varint(1)
    )
    client.receive_stream_data(7, payload, False)

    assert client.is_webtransport_ready() is False


def test_client_does_not_enable_webtransport_for_draft17_value_greater_than_one() -> None:
    """draft-17 の SETTINGS_WT_ENABLED が 1 以外なら WebTransport 対応と判定しない

    draft-17 Section 3.1 は値が 1 の場合だけ WebTransport 対応を表すと定める。
    値が 2 以上の場合の接続エラー処理は nghttp3 の未知 SETTINGS 扱いのため
    本テストでは判定だけを確認する。
    """
    client, _server = _create_pair()
    frame = _make_settings_frame(
        [
            (_ENABLE_CONNECT_PROTOCOL, 1),
            (_H3_DATAGRAM, 1),
            (_WT_ENABLED_DRAFT17, 2),
        ]
    )

    client.receive_stream_data(3, frame, False)

    assert client.is_webtransport_ready() is False
