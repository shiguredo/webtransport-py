"""WebTransport over HTTP/3 の Sans-IO API

プロトコル処理と I/O を分離した低レベル API を提供する。asyncio の高レベル API は
`webtransport.Client` / `webtransport.Server` にあり、`HTTPVersion.HTTP3` で
選ぶ。このモジュールは I/O を持たないため、テストや独自イベントループから
`webtransport.quic.Connection` と結線して使う。

提供するもの:
    - Config: セッション設定
    - Session: WebTransport セッション
    - Event: イベント
    - EventType: イベント種別
    - StreamInfo: ストリーム情報

Usage:
    from webtransport.h3 import Config, Session, EventType

    # asyncio の高レベル API
    from webtransport import Client, HTTPVersion, Server
"""

from webtransport.h3 import exceptions
from webtransport.webtransport_ext.h3 import (
    Config,
    Event,
    EventType,
    Session,
    StreamInfo,
)

__all__ = [
    "Config",
    "Event",
    "EventType",
    "Session",
    "StreamInfo",
    "exceptions",
]
