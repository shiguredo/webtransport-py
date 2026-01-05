"""HTTP/2 API

Sans-IO 低レベル API と asyncio 高レベル API を提供する。

低レベル API (Sans-IO):
    - Config: 接続設定
    - Connection: HTTP/2 接続
    - Event: イベント
    - EventType: イベント種別

高レベル API (asyncio + TCP/TLS):
    - Server: HTTP/2 サーバー
    - Client: HTTP/2 クライアント

Usage:
    # 低レベル API
    from webtransport.http2 import Config, Connection, EventType

    # 高レベル API
    from webtransport.http2 import Server, Client
"""

from webtransport.webtransport_ext.http2 import (
    Config,
    Connection,
    Event,
    EventType,
    get_version,
)
from webtransport.http2.server import Server
from webtransport.http2.client import Client

__all__ = [
    "Config",
    "Connection",
    "Event",
    "EventType",
    "get_version",
    "Server",
    "Client",
]
