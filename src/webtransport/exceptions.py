"""webtransport が送出する例外の基底

層ごとの具体例外は各サブモジュールに置く。

- `webtransport.quic.exceptions`: QUIC (RFC 9000 / RFC 9001)
- `webtransport.http2.exceptions`: HTTP/2 (RFC 9113)
- `webtransport.http3.exceptions`: HTTP/3 (RFC 9114 / RFC 9204)
- `webtransport.h2.exceptions`: WebTransport over HTTP/2
- `webtransport.h3.exceptions`: WebTransport over HTTP/3

`ConnectTimeoutError` だけは層に依存しない (接続の成否が期限までに確定しなかった
ことだけを表し、原因が UDP の無応答なのか TCP の half-open なのかを区別しない)
ため、ここに置く。接続が拒否された場合やハンドシェイクが失敗した場合は、
原因となった層の例外 (`webtransport.quic.exceptions.QuicHandshakeError` など) を
送出する。

属性の規約:

- `error_code`: ワイヤ上のエラーコードをそのまま `int` で持つ。比較用の
  `IntEnum` は各層のモジュールにある (`QuicTransportErrorCode` など)
- `reason`: ピアが付けた reason phrase、またはライブラリが付けた説明
- `stream_id` / `frame_type` / `status_code`: 層ごとに意味を持つ追加情報
"""

from __future__ import annotations

import enum

__all__ = [
    "ConnectFailedError",
    "ConnectTimeoutError",
    "WebTransportError",
]


class WebTransportError(Exception):
    """webtransport が送出するすべての例外の基底クラス"""


class ConnectFailedError(WebTransportError):
    """接続を確立する前の段階で失敗した場合の例外

    名前解決の失敗や接続オブジェクトの生成失敗など、プロトコル層に到達する前に
    判明した失敗を表す。接続が拒否された場合やハンドシェイクが失敗した場合は、
    原因となった層の例外を送出する。
    """


class ConnectTimeoutError(WebTransportError):
    """指定 `timeout` を超えても接続の成否が確定しなかった場合の例外

    待機中に成否を決める具体イベントが 1 つも届かず deadline に達した
    ケース (UDP blackhole や TCP half-open のような完全無応答) で送出する。
    接続が拒否された場合・ハンドシェイクが失敗した場合は、原因となった層の
    例外を送出する。
    """


def error_code_name(error_code: int, table: type[enum.IntEnum]) -> str:
    """エラーコードを `NAME (0xXX)` の形にする

    内部ヘルパー。`table` に対応する値が無い場合は 16 進表記だけを返す。

    Args:
        error_code: ワイヤ上のエラーコード
        table: エラーコードの `IntEnum`

    Returns:
        エラーコードの表示用文字列
    """
    try:
        member = table(error_code)
    except ValueError:
        return f"0x{error_code:x}"
    return f"{member.name} (0x{error_code:x})"
