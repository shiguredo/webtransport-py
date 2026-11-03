"""WebTransport over HTTP/2 の SETTINGS_WT_ENABLED codepoint 互換テスト

draft-16 で SETTINGS_WT_ENABLED の codepoint は 0x2b60 から 0x2b67 に変更
された (draft-16 Section 11.2)。本実装は draft-16 の 0x2b67 を送出し、旧
draft-15 の 0x2b60 も併送・受理して旧実装との相互運用を維持する。

ここでは、送出する SETTINGS に両方の codepoint が含まれること、受信側が
どちらの codepoint でも WebTransport 対応として扱うことを検証する。
SETTINGS はワイヤ注入で再現する (公開 API では任意の codepoint の SETTINGS を
送出する手段が存在しないため)。
"""

import struct

from webtransport import h2

_PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
_FRAME_TYPE_SETTINGS = 0x04
_SETTINGS_ENABLE_CONNECT_PROTOCOL = 0x08
_SETTINGS_WT_ENABLED = 0x2B67
_SETTINGS_WT_ENABLED_DRAFT15 = 0x2B60


def _encode_settings_frame(entries: list[tuple[int, int]]) -> bytes:
    """SETTINGS フレーム (type=0x04) を組み立てる"""
    payload = b"".join(struct.pack(">HI", identifier, value) for identifier, value in entries)
    return (
        len(payload).to_bytes(3, "big")
        + bytes([_FRAME_TYPE_SETTINGS, 0x00])
        + b"\x00\x00\x00\x00"
        + payload
    )


def _parse_settings_entries(wire: bytes) -> dict[int, int]:
    """SETTINGS フレームの設定エントリを辞書で返す

    クライアントの send() は connection preface を先頭に含むため読み飛ばす。
    """
    index = len(_PREFACE) if wire.startswith(_PREFACE) else 0
    while index + 9 <= len(wire):
        length = int.from_bytes(wire[index : index + 3], "big")
        frame_type = wire[index + 3]
        payload = wire[index + 9 : index + 9 + length]
        if frame_type == _FRAME_TYPE_SETTINGS:
            return {
                int.from_bytes(payload[offset : offset + 2], "big"): int.from_bytes(
                    payload[offset + 2 : offset + 6], "big"
                )
                for offset in range(0, len(payload) - 5, 6)
            }
        index += 9 + length
    raise AssertionError("SETTINGS フレームが見つかりません")


def _assert_both_codepoints_sent(factory) -> None:
    """送出する SETTINGS に draft-16 と draft-15 の codepoint が含まれることを確認"""
    session = factory()
    entries = _parse_settings_entries(session.send())
    assert entries.get(_SETTINGS_WT_ENABLED) == 1, (
        "draft-16 の SETTINGS_WT_ENABLED (0x2b67) が送出されていません"
    )
    assert entries.get(_SETTINGS_WT_ENABLED_DRAFT15) == 1, (
        "旧 codepoint の SETTINGS_WT_ENABLED (0x2b60) が併送されていません"
    )


def test_client_sends_both_wt_enabled_codepoints() -> None:
    """クライアントが draft-16 と draft-15 の SETTINGS_WT_ENABLED を併送することを確認"""
    _assert_both_codepoints_sent(lambda: h2.Session.create_client(h2.Config()))


def test_server_sends_both_wt_enabled_codepoints() -> None:
    """サーバーが draft-16 と draft-15 の SETTINGS_WT_ENABLED を併送することを確認"""
    config = h2.Config()
    config.is_server = True
    _assert_both_codepoints_sent(lambda: h2.Session.create_server(config))


def _assert_client_accepts_codepoint(entries: list[tuple[int, int]]) -> None:
    """クライアントが指定の SETTINGS を WebTransport 対応として受理することを確認"""
    client = h2.Session.create_client(h2.Config())
    client.receive(_encode_settings_frame(entries))
    assert client.is_webtransport_ready() is True, "WebTransport 対応として扱われていません"


def test_client_accepts_draft16_codepoint() -> None:
    """クライアントが draft-16 の codepoint を WebTransport 対応として受理することを確認"""
    _assert_client_accepts_codepoint(
        [
            (_SETTINGS_ENABLE_CONNECT_PROTOCOL, 1),
            (_SETTINGS_WT_ENABLED, 1),
        ]
    )


def test_client_accepts_draft15_codepoint() -> None:
    """クライアントが旧 draft-15 の codepoint を WebTransport 対応として受理することを確認"""
    _assert_client_accepts_codepoint(
        [
            (_SETTINGS_ENABLE_CONNECT_PROTOCOL, 1),
            (_SETTINGS_WT_ENABLED_DRAFT15, 1),
        ]
    )


def test_client_accepts_both_codepoints() -> None:
    """クライアントが両方の codepoint を含む SETTINGS を受理することを確認"""
    _assert_client_accepts_codepoint(
        [
            (_SETTINGS_ENABLE_CONNECT_PROTOCOL, 1),
            (_SETTINGS_WT_ENABLED, 1),
            (_SETTINGS_WT_ENABLED_DRAFT15, 1),
        ]
    )


def test_client_rejects_unknown_setting_as_webtransport_disabled() -> None:
    """WT_ENABLED を含まない SETTINGS では WebTransport 対応にならないことを確認

    受理判定が codepoint によらず真になっていないこと (空振りでないこと) を
    確認する。
    """
    client = h2.Session.create_client(h2.Config())
    client.receive(_encode_settings_frame([(_SETTINGS_ENABLE_CONNECT_PROTOCOL, 1)]))
    assert client.is_webtransport_ready() is False


def _assert_server_accepts_codepoint(entries: list[tuple[int, int]]) -> None:
    """サーバーがクライアントの指定の SETTINGS を WebTransport 対応として受理することを確認"""
    config = h2.Config()
    config.is_server = True
    server = h2.Session.create_server(config)
    server.receive(_PREFACE + _encode_settings_frame(entries))
    assert server.is_webtransport_ready() is True, "WebTransport 対応として扱われていません"


def test_server_accepts_draft16_codepoint() -> None:
    """サーバーが draft-16 の codepoint を WebTransport 対応として受理することを確認"""
    _assert_server_accepts_codepoint(
        [
            (_SETTINGS_ENABLE_CONNECT_PROTOCOL, 1),
            (_SETTINGS_WT_ENABLED, 1),
        ]
    )


def test_server_accepts_draft15_codepoint() -> None:
    """サーバーが旧 draft-15 の codepoint を WebTransport 対応として受理することを確認"""
    _assert_server_accepts_codepoint(
        [
            (_SETTINGS_ENABLE_CONNECT_PROTOCOL, 1),
            (_SETTINGS_WT_ENABLED_DRAFT15, 1),
        ]
    )
