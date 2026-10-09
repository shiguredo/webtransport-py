"""HTTP/3 層の例外とエラーコード

RFC 9114 Section 8.1 と RFC 9204 Section 8.3 のエラーコード、および
draft-ietf-webtrans-http3-16 Section 9.5 が登録する WebTransport 用のコードを
`IntEnum` として提供し、接続の失敗を例外として扱えるようにする。

HTTP/3 は QUIC 上で動くため、QUIC 層のエラー (`webtransport.quic.exceptions`) と
区別して扱う。ストリーム単位のエラー (RESET_STREAM / STOP_SENDING) と GOAWAY は
低レベルの `Event` と `on_stream_reset` / `on_goaway` で通知されるため、例外には
しない (GOAWAY は graceful shutdown の通知であり、接続は継続できる)。
"""

import enum

from webtransport.exceptions import WebTransportError, error_code_name

__all__ = [
    "Http3ConnectionError",
    "Http3Error",
    "Http3ErrorCode",
]


class Http3Error(WebTransportError):
    """HTTP/3 層のエラーの基底クラス"""


class Http3ErrorCode(enum.IntEnum):
    """HTTP/3 のエラーコード (RFC 9114 Section 8.1 / RFC 9204 Section 8.3)

    WebTransport 用の 3 値は draft-ietf-webtrans-http3-16 Section 9.5 が
    HTTP/3 のエラーコードレジストリへ登録するものである。
    """

    NO_ERROR = 0x0100
    GENERAL_PROTOCOL_ERROR = 0x0101
    INTERNAL_ERROR = 0x0102
    STREAM_CREATION_ERROR = 0x0103
    CLOSED_CRITICAL_STREAM = 0x0104
    FRAME_UNEXPECTED = 0x0105
    FRAME_ERROR = 0x0106
    EXCESSIVE_LOAD = 0x0107
    ID_ERROR = 0x0108
    SETTINGS_ERROR = 0x0109
    MISSING_SETTINGS = 0x010A
    REQUEST_REJECTED = 0x010B
    REQUEST_CANCELLED = 0x010C
    REQUEST_INCOMPLETE = 0x010D
    MESSAGE_ERROR = 0x010E
    CONNECT_ERROR = 0x010F
    VERSION_FALLBACK = 0x0110
    # RFC 9204 Section 8.3 (QPACK)
    QPACK_DECOMPRESSION_FAILED = 0x0200
    QPACK_ENCODER_STREAM_ERROR = 0x0201
    QPACK_DECODER_STREAM_ERROR = 0x0202
    # draft-ietf-webtrans-http3-16 Section 9.5
    WT_FLOW_CONTROL_ERROR = 0x045D4487
    WT_SESSION_GONE = 0x170D7B68
    WT_BUFFERED_STREAM_REJECTED = 0x3994BD84


class Http3ConnectionError(Http3Error):
    """HTTP/3 の接続がエラーで終了した場合の例外

    Attributes:
        error_code: エラーコード。ローカルで検知した終了では 0
        reason: 終了理由
        frame_type: エラーを引き起こしたフレーム種別。不明な場合は -1
    """

    def __init__(self, error_code: int = 0, reason: str = "", *, frame_type: int = -1) -> None:
        super().__init__(error_code, reason, frame_type)
        self.error_code = error_code
        self.reason = reason
        self.frame_type = frame_type

    def __str__(self) -> str:
        text = f"HTTP/3 connection error {error_code_name(self.error_code, Http3ErrorCode)}"
        if self.frame_type >= 0:
            text += f" frame=0x{self.frame_type:x}"
        if self.reason:
            text += f": {self.reason}"
        return text
