"""HTTP/2 層の例外とエラーコード

RFC 9113 Section 7 のエラーコードを `IntEnum` として提供し、接続の失敗を例外と
して扱えるようにする。

HTTP/2 は TLS over TCP 上で動くため、ハンドシェイクの失敗は
`Http2HandshakeError` で表す。ストリーム単位のエラー (RST_STREAM) と GOAWAY は
低レベルの `Event` と `on_stream_reset` / `on_goaway` で通知されるため、例外には
しない (接続を終わらせず、同じ接続で回復できるためである)。
"""

import enum

from webtransport.exceptions import WebTransportError, error_code_name

__all__ = [
    "Http2ConnectionError",
    "Http2Error",
    "Http2ErrorCode",
    "Http2HandshakeError",
]


class Http2Error(WebTransportError):
    """HTTP/2 層のエラーの基底クラス"""


class Http2ErrorCode(enum.IntEnum):
    """HTTP/2 のエラーコード (RFC 9113 Section 7)"""

    NO_ERROR = 0x00
    PROTOCOL_ERROR = 0x01
    INTERNAL_ERROR = 0x02
    FLOW_CONTROL_ERROR = 0x03
    SETTINGS_TIMEOUT = 0x04
    STREAM_CLOSED = 0x05
    FRAME_SIZE_ERROR = 0x06
    REFUSED_STREAM = 0x07
    CANCEL = 0x08
    COMPRESSION_ERROR = 0x09
    CONNECT_ERROR = 0x0A
    ENHANCE_YOUR_CALM = 0x0B
    INADEQUATE_SECURITY = 0x0C
    HTTP_1_1_REQUIRED = 0x0D


class Http2ConnectionError(Http2Error):
    """HTTP/2 の接続がエラーで終了した場合の例外

    Attributes:
        error_code: エラーコード。ローカルで検知した終了では 0
        reason: 終了理由
    """

    def __init__(self, error_code: int = 0, reason: str = "") -> None:
        super().__init__(error_code, reason)
        self.error_code = error_code
        self.reason = reason

    def __str__(self) -> str:
        text = f"HTTP/2 connection error {error_code_name(self.error_code, Http2ErrorCode)}"
        if self.reason:
            text += f": {self.reason}"
        return text


class Http2HandshakeError(Http2Error):
    """TLS ハンドシェイクが失敗した場合の例外

    Attributes:
        reason: 失敗の説明
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)
        self.reason = reason

    def __str__(self) -> str:
        return (
            f"HTTP/2 handshake failed: {self.reason}" if self.reason else "HTTP/2 handshake failed"
        )
