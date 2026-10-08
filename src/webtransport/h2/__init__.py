"""WebTransport over HTTP/2 の Sans-IO API

プロトコル処理と I/O を分離した低レベル API を提供する。Capsule Protocol
(RFC 9297) によりストリームと DATAGRAM を多重化する。asyncio の高レベル API は
`webtransport.Client` / `webtransport.Server` にあり、`HTTPVersion.HTTP2` で
選ぶ。このモジュールは I/O を持たないため、テストや独自イベントループから
`webtransport.http2.Connection` と結線して使う。

提供するもの:
    - Config: セッション設定
    - Session: WebTransport セッション
    - Event: イベント
    - EventType: イベント種別
    - WtErrorCode: WebTransport のエラーコード

Usage:
    from webtransport.h2 import Config, Session, EventType

    # asyncio の高レベル API
    from webtransport import Client, HTTPVersion, Server
"""

from webtransport.h2 import exceptions
from webtransport.webtransport_ext.h2 import (
    Config,
    Event,
    EventType,
    Session,
    WtErrorCode,
)

__all__ = [
    "Config",
    "Event",
    "EventType",
    "Session",
    "WtErrorCode",
    "exceptions",
]
