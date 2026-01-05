"""WebTransport over HTTP/3 API

Sans-IO 低レベル API と asyncio 高レベル API を提供する。

低レベル API (Sans-IO):
    - Config: セッション設定
    - Session: WebTransport セッション
    - Event: イベント
    - EventType: イベント種別
    - StreamInfo: ストリーム情報

高レベル API (asyncio + UDP):
    - Server: WebTransport サーバー
    - Client: WebTransport クライアント

Usage:
    # 低レベル API
    from webtransport.h3 import Config, Session, EventType

    # 高レベル API
    from webtransport.h3 import Server, Client
"""

from webtransport.webtransport_ext.h3 import (
    Config,
    Event,
    EventType,
    Session,
    StreamInfo,
)
from webtransport.h3.server import Server
from webtransport.h3.client import Client

__all__ = [
    "Config",
    "Event",
    "EventType",
    "Session",
    "StreamInfo",
    "Server",
    "Client",
]
