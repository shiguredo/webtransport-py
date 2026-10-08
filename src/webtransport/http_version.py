"""WebTransport が使う HTTP バージョン

WebTransport は HTTP/3 (QUIC 上) と HTTP/2 (TCP + TLS 上の Capsule Protocol) の
2 通りで多重化する。どちらを使うかをモジュールの選択ではなく値で表すための
enum をここに置く。
"""

from __future__ import annotations

import enum

__all__ = ["HTTPVersion"]


class HTTPVersion(enum.Enum):
    """WebTransport が使う HTTP バージョン

    値は ALPN のプロトコル識別子と同じにする (そのまま `alpn_protocols` へ
    渡せる)。

    - `HTTP2`: WebTransport over HTTP/2 (RFC 9297 Capsule Protocol、TCP + TLS)
    - `HTTP3`: WebTransport over HTTP/3 (draft-ietf-webtrans-http3、UDP + QUIC)
    """

    HTTP2 = "h2"
    HTTP3 = "h3"
