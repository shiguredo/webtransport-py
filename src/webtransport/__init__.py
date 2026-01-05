"""WebTransport

QUIC/HTTP2/HTTP3/WebTransport のバインディングと高レベル API を提供する。

各モジュールは低レベル Sans-IO API と高レベル asyncio API の両方を含む。

Usage:
    # QUIC (低レベル + 高レベル API)
    from webtransport import quic
    from webtransport.quic import Server, Client

    # HTTP/2 (低レベル + 高レベル API)
    from webtransport import http2
    from webtransport.http2 import Server, Client

    # HTTP/3 (低レベル + 高レベル API)
    from webtransport import http3
    from webtransport.http3 import Server, Client

    # WebTransport over HTTP/3 (低レベル + 高レベル API)
    from webtransport import h3
    from webtransport.h3 import Server, Client

    # WebTransport over HTTP/2 (低レベル + 高レベル API)
    from webtransport import h2
    from webtransport.h2 import Server, Client
"""

from webtransport import quic
from webtransport import http2
from webtransport import http3
from webtransport import h3
from webtransport import h2

__all__ = [
    "quic",
    "http2",
    "http3",
    "h3",
    "h2",
]
