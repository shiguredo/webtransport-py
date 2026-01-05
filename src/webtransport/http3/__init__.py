"""HTTP/3 API

Sans-IO 低レベル API と asyncio 高レベル API を提供する。

低レベル API (Sans-IO):
    - Config: 接続設定
    - Connection: HTTP/3 接続
    - Event: イベント
    - EventType: イベント種別

高レベル API (asyncio + UDP):
    - Server: HTTP/3 サーバー
    - Client: HTTP/3 クライアント

Usage:
    # 低レベル API
    from webtransport.http3 import Config, Connection, EventType

    # 高レベル API
    from webtransport.http3 import Server, Client
"""

from webtransport.webtransport_ext.http3 import (
    Config,
    Connection,
    Event,
    EventType,
    get_version,
)
from webtransport.http3.server import Server
from webtransport.http3.client import Client

__all__ = [
    "Config",
    "Connection",
    "Event",
    "EventType",
    "get_version",
    "Server",
    "Client",
]
