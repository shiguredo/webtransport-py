"""WebTransport 接続エラーの例外階層

`h3.Client.connect` と `h2.Client.connect` は deadline ベースで bounded に
動作し、失敗時は真偽値ではなくこのモジュールの具体例外で理由を通知する。
"""

from __future__ import annotations

__all__ = [
    "ConnectRefusedError",
    "ConnectTimeoutError",
    "HandshakeFailedError",
    "WebTransportConnectError",
]


class WebTransportConnectError(Exception):
    """すべての `connect()` 失敗例外の基底クラス"""


class ConnectTimeoutError(WebTransportConnectError):
    """指定 `timeout` を超えても成否が確定しなかった場合の例外

    待機中に成否を決める具体イベントが 1 つも届かず deadline に達した
    ケース (UDP blackhole や TCP half-open のような完全無応答) で送出する。
    """


class ConnectRefusedError(WebTransportConnectError):
    """接続が拒否された場合の例外

    待機中に TCP RST、QUIC 側の明示的な `CONNECTION_CLOSE`、または
    TLS ハンドシェイクの前段での接続拒否が届いた場合に送出する。
    同一の `CONNECTION_CLOSE` でも、QUIC ハンドシェイク完了前のものは
    TLS 由来とみなして `HandshakeFailedError` に寄せる。
    """


class HandshakeFailedError(WebTransportConnectError):
    """ハンドシェイクまたはセッション確立の意味的検証に失敗した場合の例外

    TLS ハンドシェイクの検証失敗 (証明書検証エラー・ALPN 不一致など) や
    transport parameter の要件未達、Extended CONNECT のセマンティック失敗
    (非 2xx 応答) の場合に送出する。
    """
