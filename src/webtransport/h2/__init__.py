"""WebTransport over HTTP/2 API

Sans-IO 低レベル API と asyncio 高レベル API を提供する。
Capsule Protocol (RFC 9297) によりストリームと DATAGRAM を多重化する。

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

from webtransport.h2.client import Client
from webtransport.h2.server import Server, SessionWriter
from webtransport.webtransport_ext.h2 import (
    Config,
    Event,
    EventType,
    Session,
)

__all__ = [
    "Client",
    "Config",
    "Event",
    "EventType",
    "Server",
    "Session",
    "SessionWriter",
]
