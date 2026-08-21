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
"""

from __future__ import annotations

__all__ = ["H3_GENERAL_PROTOCOL_ERROR"]

H3_GENERAL_PROTOCOL_ERROR: int = 0x0101
