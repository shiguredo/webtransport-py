"""WebTransport over HTTP/2 API

Sans-IO 低レベル API と asyncio 高レベル API を提供する。
HTTP/2 は DATAGRAM をサポートしないため、ストリームのみ使用可能。

低レベル API (Sans-IO):
    - Config: セッション設定
    - Session: WebTransport セッション
    - Event: イベント
    - EventType: イベント種別

高レベル API (asyncio + TCP/TLS):
    - Server: WebTransport サーバー
    - Client: WebTransport クライアント

Usage:
    # 低レベル API
    from webtransport.h2 import Config, Session, EventType

    # 高レベル API
    from webtransport.h2 import Server, Client
"""

from webtransport.webtransport_ext.h2 import (
    Config,
    Event,
    EventType,
    Session,
)
from webtransport.h2.server import Server
from webtransport.h2.client import Client

__all__ = [
    "Config",
    "Event",
    "EventType",
    "Session",
    "Server",
    "Client",
]
