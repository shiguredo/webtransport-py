"""QUIC API

Sans-IO 低レベル API と asyncio 高レベル API を提供する。

低レベル API (Sans-IO):
    - Config: 接続設定
    - Connection: QUIC 接続
    - Event: イベント
    - EventType: イベント種別

高レベル API (asyncio + UDP):
    - Server: QUIC サーバー
    - Client: QUIC クライアント

Usage:
    # 低レベル API
    from webtransport.quic import Config, Connection, EventType

    # 高レベル API
    from webtransport.quic import Server, Client
"""

from webtransport.webtransport_ext.quic import (
    Config,
    Connection,
    Event,
    EventType,
    get_version,
)
from webtransport.quic.server import Server
from webtransport.quic.client import Client

__all__ = [
    "Config",
    "Connection",
    "Event",
    "EventType",
    "get_version",
    "Server",
    "Client",
]
