"""WebTransport over HTTP/3 の例外とエラーコード

draft-ietf-webtrans-http3-16 が定義するセッションの拒否と終了を例外として
扱えるようにする。

セッションの終了は 2 通りある。Extended CONNECT の応答が非 2xx だった場合は
`WebTransportSessionRejectedError`、確立後の終了は
`WebTransportSessionClosedError` (WT_CLOSE_SESSION カプセルまたは CONNECT
ストリームのクローズ) である。データストリームのリセットは
`WebTransportStreamError` で表し、エラーコードはアプリケーションコードである
(Section 4.4 の WT_APPLICATION_ERROR レンジへの載せ替え前の値)。
"""

import enum

from webtransport.exceptions import WebTransportError, error_code_name

__all__ = [
    "WebTransportErrorCode",
    "WebTransportH3Error",
    "WebTransportProtocolError",
    "WebTransportSessionClosedError",
    "WebTransportSessionRejectedError",
]


class WebTransportErrorCode(enum.IntEnum):
    """WebTransport over HTTP/3 が使う HTTP/3 のエラーコード

    draft-ietf-webtrans-http3-16 Section 9.5 が登録する値であり、
    `webtransport.http3.exceptions.Http3ErrorCode` の部分集合である。
    """

    WT_FLOW_CONTROL_ERROR = 0x045D4487
    WT_SESSION_GONE = 0x170D7B68
    WT_BUFFERED_STREAM_REJECTED = 0x3994BD84


class WebTransportH3Error(WebTransportError):
    """WebTransport over HTTP/3 層のエラーの基底クラス"""


class WebTransportSessionRejectedError(WebTransportH3Error):
    """セッションが拒否された場合の例外

    Extended CONNECT の応答が非 2xx だった場合 (draft-ietf-webtrans-http3-16
    Section 3.2)。

    Attributes:
        status_code: 応答の HTTP status code
        headers: 応答のヘッダー
    """

    def __init__(self, status_code: int = 0, headers: tuple[tuple[str, str], ...] = ()) -> None:
        super().__init__(status_code, headers)
        self.status_code = status_code
        self.headers = headers

    def __str__(self) -> str:
        return f"WebTransport session rejected with status {self.status_code}"


class WebTransportSessionClosedError(WebTransportH3Error):
    """セッションが終了した場合の例外

    WT_CLOSE_SESSION カプセルの受信 (draft-ietf-webtrans-http3-16 Section 4.4)
    または CONNECT ストリームのクローズ。

    Attributes:
        error_code: セッションの終了コード
        reason: 終了理由
    """

    def __init__(self, error_code: int = 0, reason: str = "") -> None:
        super().__init__(error_code, reason)
        self.error_code = error_code
        self.reason = reason

    def __str__(self) -> str:
        text = f"WebTransport session closed with error 0x{self.error_code:x}"
        if self.reason:
            text += f": {self.reason}"
        return text


class WebTransportProtocolError(WebTransportH3Error):
    """WebTransport over HTTP/3 層のプロトコルエラーを検知した場合の例外

    低レベルの `h3.Session` が検知したエラー (HTTP/3 の ERROR イベント) に対応する。

    Attributes:
        error_code: HTTP/3 のエラーコード
        error_message: エラーの説明
    """

    def __init__(self, error_code: int = 0, error_message: str = "") -> None:
        super().__init__(error_code, error_message)
        self.error_code = error_code
        self.error_message = error_message

    def __str__(self) -> str:
        text = (
            f"WebTransport protocol error {error_code_name(self.error_code, WebTransportErrorCode)}"
        )
        if self.error_message:
            text += f": {self.error_message}"
        return text
