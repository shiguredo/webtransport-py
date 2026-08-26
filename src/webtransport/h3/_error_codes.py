"""WebTransport over HTTP/3 のアプリケーションエラーコード変換

draft-ietf-webtrans-http3-16 Section 4.4 / Figure 4 の変換を実装する。
仕様が改訂された場合はこのモジュールを見直すこと。
"""

from __future__ import annotations

# WT_APPLICATION_ERROR レンジ (draft-16 Section 4.4 / Section 9.5)
_WT_APPLICATION_ERROR_FIRST = 0x52E4A40FA8DB
_WT_APPLICATION_ERROR_LAST = 0x52E5AC983162

# アプリが渡せるエラーコードの上限 (32bit)
_MAX_APPLICATION_ERROR_CODE = 0xFFFFFFFF


def webtransport_code_to_http_code(n: int) -> int:
    """アプリの 32bit エラーコードを WT_APPLICATION_ERROR レンジへ変換する

    draft-ietf-webtrans-http3-16 Figure 4 の
    ``webtransport_code_to_http_code``。予約済みコードポイント
    (``0x1f * N + 0x21``) をスキップする。

    Args:
        n: アプリケーションエラーコード (0 〜 0xffffffff)

    Returns:
        ワイヤに載せる HTTP/3 エラーコード

    Raises:
        ValueError: n が 32bit 範囲外の場合
    """
    if n < 0 or n > _MAX_APPLICATION_ERROR_CODE:
        raise ValueError(
            f"application error code out of uint32 range: expected 0..{_MAX_APPLICATION_ERROR_CODE}, got {n}"
        )
    return _WT_APPLICATION_ERROR_FIRST + n + (n // 0x1E)


def http_code_to_webtransport_code(h: int) -> int:
    """WT_APPLICATION_ERROR レンジのワイヤコードをアプリコードへ逆変換する

    draft-ietf-webtrans-http3-16 Figure 4 の
    ``http_code_to_webtransport_code``。ライブラリの受信配信では使わず、
    アプリやテストがワイヤコードを解釈する際の参照実装として提供する。

    Args:
        h: ワイヤ上の HTTP/3 エラーコード

    Returns:
        アプリケーションエラーコード

    Raises:
        ValueError: レンジ外、または予約済みコードポイントの場合
    """
    if not is_wt_application_error_code(h):
        raise ValueError(
            f"HTTP/3 error code outside WT_APPLICATION_ERROR range or reserved: got {h:#x}"
        )
    shifted = h - _WT_APPLICATION_ERROR_FIRST
    return shifted - (shifted // 0x1F)


def is_wt_application_error_code(h: int) -> bool:
    """ワイヤコードが WT_APPLICATION_ERROR レンジの非予約コードか判定する

    予約済み (``0x1f * N + 0x21``) はレンジ内でも False を返す。
    """
    if h < _WT_APPLICATION_ERROR_FIRST or h > _WT_APPLICATION_ERROR_LAST:
        return False
    # HTTP/3 の予約済みコードポイント (RFC 9114 Section 8.1)
    return (h - 0x21) % 0x1F != 0


def deliver_stream_reset_error_code(
    *,
    wire_error_code: int,
    is_connect_stream: bool,
) -> int | None:
    """受信したストリームリセットのエラーコードをアプリへ配信する形へ整える

    draft-ietf-webtrans-http3-16 Section 4.4:
    - データストリーム: ワイヤコードを変更せずに配信する。レンジ外
      (予約済み含む) はアプリエラーコードなし (None)
    - CONNECT ストリーム: HTTP/3 エラーコード空間のまま配信する
      (リマップ対象外)
    """
    if is_connect_stream:
        return wire_error_code
    if is_wt_application_error_code(wire_error_code):
        return wire_error_code
    return None
