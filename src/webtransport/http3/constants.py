"""HTTP/3 プロトコル定数

RFC 9114 Section 8.1 で定義されている HTTP/3 error code の定数を提供する。
`__init__.py` / `client.py` / `server.py` から循環 import なしで参照できるよう、
Client / Server に依存しない独立モジュールとして分離している。

RFC 9114 Section 8.1 (H3_GENERAL_PROTOCOL_ERROR):
    Peer violated protocol requirements in a way that does not match a more
    specific error code, or endpoint declines to use the more specific error code.

nghttp3 の read_stream2 / writev_stream が負値 return する状況 (HTTP/3
プロトコル違反の検知) と意味的に整合するため、bindings で `closed_ = true` に
昇格したあと高レベル層が QUIC CONNECTION_CLOSE に載せる error_code として使う。

RFC 9114 Section 8.1 (H3_NO_ERROR):
    No error.  This is used when the connection or stream needs to be closed,
    but there is no error to signal.

低レベル `http3.Connection.close_stream` の既定 error_code として使う。

RFC 9114 Section 8.1 (H3_REQUEST_CANCELLED):
    The request or its response (including pushed response) is cancelled.

RFC 9114 Section 4.1.1 により、部分的に処理したリクエストを破棄する場合は
H3_REQUEST_CANCELLED を使う。テストで `close_stream` の明示値を与えるために
公開する。
"""

from __future__ import annotations

__all__ = [
    "H3_GENERAL_PROTOCOL_ERROR",
    "H3_NO_ERROR",
    "H3_REQUEST_CANCELLED",
]

H3_GENERAL_PROTOCOL_ERROR: int = 0x0101
H3_NO_ERROR: int = 0x0100
H3_REQUEST_CANCELLED: int = 0x010C
