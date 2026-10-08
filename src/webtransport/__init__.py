"""WebTransport

QUIC/HTTP2/HTTP3/WebTransport のバインディングと高レベル API を提供する。

各モジュールは低レベル Sans-IO API と高レベル asyncio API の両方を含む。
層ごとの例外は各モジュールの `exceptions` サブモジュールにある。

Usage:
    # QUIC (低レベル + 高レベル API)
    from webtransport import quic
    from webtransport.quic import Server, Client
    from webtransport.quic.exceptions import QuicConnectionError

    # HTTP/2 (低レベル + 高レベル API)
    from webtransport import http2
    from webtransport.http2 import Server, Client
    from webtransport.http2.exceptions import Http2GoAwayError

    # HTTP/3 (低レベル + 高レベル API)
    from webtransport import http3
    from webtransport.http3 import Server, Client
    from webtransport.http3.exceptions import Http3ErrorCode

    # WebTransport over HTTP/3 (低レベル + 高レベル API)
    from webtransport import h3
    from webtransport.h3 import Server, Client
    from webtransport.h3.exceptions import WebTransportSessionClosedError

    # WebTransport over HTTP/2 (低レベル + 高レベル API)
    from webtransport import h2
    from webtransport.h2 import Server, Client
    from webtransport.h2.exceptions import WebTransportSessionRejectedError
"""

from webtransport import h2, h3, http2, http3, quic
from webtransport.client import Client
from webtransport.exceptions import (
    ConnectFailedError,
    ConnectTimeoutError,
    WebTransportError,
)
from webtransport.http_version import HTTPVersion
from webtransport.server import Server, Session

__all__ = [
    "Client",
    "ConnectFailedError",
    "ConnectTimeoutError",
    "HTTPVersion",
    "Server",
    "Session",
    "WebTransportError",
    "h2",
    "h3",
    "http2",
    "http3",
    "quic",
]
