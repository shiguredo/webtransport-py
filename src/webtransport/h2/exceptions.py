"""WebTransport over HTTP/2 の例外とエラーコード

draft-ietf-webtrans-http2-15 が定義するセッションの拒否と終了、カプセル
プロトコルの違反を例外として扱えるようにする。

セッションの終了は 2 通りある。Extended CONNECT の応答が非 2xx だった場合は
`WebTransportSessionRejectedError`、確立後の終了は
`WebTransportSessionClosedError` (WT_CLOSE_SESSION カプセルまたは CONNECT
ストリームのリセット) である。

HTTP/2 層のエラー (`webtransport.http2.exceptions`) とは独立に扱う。
WebTransport のセッションエラーは HTTP/2 の接続状態を変えない
(draft-ietf-webtrans-http2-15 Section 3.4)。
"""

from __future__ import annotations

import enum

from webtransport.exceptions import WebTransportError, error_code_name

__all__ = [
    "WebTransportErrorCode",
    "WebTransportH2Error",
    "WebTransportProtocolError",
    "WebTransportSessionClosedError",
    "WebTransportSessionRejectedError",
]


class WebTransportErrorCode(enum.IntEnum):
    """WebTransport over HTTP/2 が使う HTTP/2 のエラーコード

    draft-ietf-webtrans-http2-15 Section 3.4 が予約する値である。draft-15 では
    0xTBD のプレースホルダであり、本ライブラリは 0x50 / 0x51 / 0x52 を使う
    (低レベルの `h2.WtErrorCode` と同じ値)。
    """

    WT_FLOW_CONTROL_ERROR = 0x50
    WT_STREAM_STATE_ERROR = 0x51
    WT_ERROR = 0x52


class WebTransportH2Error(WebTransportError):
    """WebTransport over HTTP/2 層のエラーの基底クラス"""


class WebTransportSessionRejectedError(WebTransportH2Error):
    """セッションが拒否された場合の例外

    Extended CONNECT の応答が非 2xx だった場合。HTTP/2 では CONNECT の
    応答がそのままセッションの成否になる。

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


class WebTransportSessionClosedError(WebTransportH2Error):
    """セッションが終了した場合の例外

    WT_CLOSE_SESSION カプセルの受信、または CONNECT ストリームのクローズ。

    Attributes:
        error_code: セッションの終了コード。アプリケーション由来でない場合は 0
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


class WebTransportProtocolError(WebTransportH2Error):
    """WebTransport over HTTP/2 層のプロトコルエラーを検知した場合の例外

    低レベルの `h2.Session` が `on_error` で通知するエラーに対応する。draft-15
    Section 3.4 のとおり、本ライブラリが通知するのは WT_FLOW_CONTROL_ERROR である。

    Attributes:
        error_code: WebTransport のエラーコード
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
