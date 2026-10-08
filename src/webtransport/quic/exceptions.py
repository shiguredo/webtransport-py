"""QUIC 層の例外とエラーコード

RFC 9000 Section 20.1 のトランスポートエラーコード、RFC 9001 Section 4.8 の
TLS アラート由来のコード (CRYPTO_ERROR 範囲)、RFC 9368 のバージョン交渉のコードを
`IntEnum` として提供し、接続の終了とハンドシェイクの失敗を例外として扱えるように
する。

例外にするのは接続単位の失敗だけである。ストリーム単位のエラー
(RESET_STREAM / STOP_SENDING) は `quic.EventType.STREAM_RESET` /
`STOP_SENDING` のイベントと `quic.Client.wait_for_stream_reset()` の戻り値で
通知する。ストリームのエラーは接続を終わらせず、同じ接続で回復できるためである。

エラーの観測点は 2 つある。ピアの CONNECTION_CLOSE は `quic.Connection` の
`error_code` / `reason` / `error_code_type` / `error_frame_type` プロパティから
読める。ローカルで検知した終了 (アイドルタイムアウト・ハンドシェイクタイムアウト・
致命的エラー) は `quic.EventType.CONNECTION_CLOSED` イベントの `reason` に説明が
入る。
"""

from __future__ import annotations

import enum
from typing import TYPE_CHECKING

from webtransport.exceptions import WebTransportError, error_code_name
from webtransport.webtransport_ext.quic import ConnectionErrorType

if TYPE_CHECKING:
    from webtransport.webtransport_ext.quic import Connection

__all__ = [
    "QuicApplicationError",
    "QuicConnectionError",
    "QuicCryptoErrorCode",
    "QuicError",
    "QuicHandshakeError",
    "QuicTransportError",
    "QuicTransportErrorCode",
    "connection_error",
    "quic_terminal_error",
]


class QuicError(WebTransportError):
    """QUIC 層のエラーの基底クラス"""


class QuicTransportErrorCode(enum.IntEnum):
    """QUIC のトランスポートエラーコード (RFC 9000 Section 20.1)

    `CRYPTO_ERROR` (0x0100) は TLS アラートの範囲の先頭であり、実際の値は
    `QuicCryptoErrorCode` にある。
    """

    NO_ERROR = 0x00
    INTERNAL_ERROR = 0x01
    CONNECTION_REFUSED = 0x02
    FLOW_CONTROL_ERROR = 0x03
    STREAM_LIMIT_ERROR = 0x04
    STREAM_STATE_ERROR = 0x05
    FINAL_SIZE_ERROR = 0x06
    FRAME_ENCODING_ERROR = 0x07
    TRANSPORT_PARAMETER_ERROR = 0x08
    CONNECTION_ID_LIMIT_ERROR = 0x09
    PROTOCOL_VIOLATION = 0x0A
    INVALID_TOKEN = 0x0B
    APPLICATION_ERROR = 0x0C
    CRYPTO_BUFFER_EXCEEDED = 0x0D
    KEY_UPDATE_ERROR = 0x0E
    AEAD_LIMIT_REACHED = 0x0F
    NO_VIABLE_PATH = 0x10
    # RFC 9368 Section 4 (バージョン交渉の失敗)
    VERSION_NEGOTIATION_ERROR = 0x11
    # TLS アラート由来のコードの範囲の先頭 (RFC 9000 Section 20.1)
    CRYPTO_ERROR = 0x0100


class QuicCryptoErrorCode(enum.IntEnum):
    """QUIC の CRYPTO_ERROR (RFC 9001 Section 4.8)

    TLS の AlertDescription (RFC 8446 Section 6.2) に 0x0100 を加えた値である。
    TLS 1.3 で廃止された値は含めない。
    """

    CRYPTO_ERROR = 0x0100
    UNEXPECTED_MESSAGE = 0x010A
    BAD_RECORD_MAC = 0x0114
    RECORD_OVERFLOW = 0x0116
    HANDSHAKE_FAILURE = 0x0128
    BAD_CERTIFICATE = 0x012A
    UNSUPPORTED_CERTIFICATE = 0x012B
    CERTIFICATE_REVOKED = 0x012C
    CERTIFICATE_EXPIRED = 0x012D
    CERTIFICATE_UNKNOWN = 0x012E
    ILLEGAL_PARAMETER = 0x012F
    UNKNOWN_CA = 0x0130
    ACCESS_DENIED = 0x0131
    DECODE_ERROR = 0x0132
    DECRYPT_ERROR = 0x0133
    PROTOCOL_VERSION = 0x0146
    INSUFFICIENT_SECURITY = 0x0147
    INTERNAL_ERROR = 0x0150
    INAPPROPRIATE_FALLBACK = 0x0156
    USER_CANCELED = 0x015A
    MISSING_EXTENSION = 0x016D
    UNSUPPORTED_EXTENSION = 0x016E
    CERTIFICATE_UNOBTAINABLE = 0x016F
    UNRECOGNIZED_NAME = 0x0170
    BAD_CERTIFICATE_STATUS_RESPONSE = 0x0171
    BAD_CERTIFICATE_HASH_VALUE = 0x0172
    UNKNOWN_PSK_IDENTITY = 0x0173
    CERTIFICATE_REQUIRED = 0x0174
    NO_APPLICATION_PROTOCOL = 0x0178


class QuicConnectionError(QuicError):
    """QUIC 接続が終了した場合の例外

    ピアが CONNECTION_CLOSE を送った場合はそのエラーコードと理由を持つ。ローカルで
    検知した終了 (致命的エラー・ハンドシェイクタイムアウトなど) ではエラーコードが 0
    になり、`reason` に説明が入る。

    Attributes:
        error_code: CONNECTION_CLOSE のエラーコード。ローカルで検知した
            終了では 0
        reason: ピアの reason phrase、またはライブラリが付けた終了理由
        error_code_type: `"transport"` (RFC 9000 Section 20.1) /
            `"application"` (アプリケーション定義) / `"local"` (ローカル検知)
        frame_type: エラーを引き起こしたフレーム種別。不明な場合は -1
    """

    def __init__(
        self,
        error_code: int = 0,
        reason: str = "",
        *,
        error_code_type: str = "local",
        frame_type: int = -1,
    ) -> None:
        super().__init__(error_code, reason, error_code_type, frame_type)
        self.error_code = error_code
        self.reason = reason
        self.error_code_type = error_code_type
        self.frame_type = frame_type

    def __str__(self) -> str:
        parts = [
            f"QUIC connection error {error_code_name(self.error_code, QuicTransportErrorCode)}",
            f"({self.error_code_type})",
        ]
        if self.frame_type >= 0:
            parts.append(f"frame=0x{self.frame_type:x}")
        if self.reason:
            parts.append(f": {self.reason}")
        return " ".join(parts)


class QuicTransportError(QuicConnectionError):
    """ピアがトランスポートエラーの CONNECTION_CLOSE を送った場合の例外

    RFC 9000 Section 19.19 の type 0x1c に対応する。`error_code` は
    `QuicTransportErrorCode` の値である。
    """


class QuicApplicationError(QuicConnectionError):
    """ピアがアプリケーションエラーの CONNECTION_CLOSE を送った場合の例外

    RFC 9000 Section 19.19 の type 0x1d に対応する。`error_code` はアプリケーション
    (WebTransport など) が定義する値である。
    """


class QuicHandshakeError(QuicError):
    """TLS ハンドシェイクが失敗した場合の例外

    Attributes:
        tls_error: ngtcp2 が記録した TLS 内部エラーコード。無ければ 0
        tls_alert: TLS アラート (AlertDescription)。無ければ 0
        reason: 失敗の説明
    """

    def __init__(self, reason: str = "", *, tls_error: int = 0, tls_alert: int = 0) -> None:
        super().__init__(reason, tls_error, tls_alert)
        self.reason = reason
        self.tls_error = tls_error
        self.tls_alert = tls_alert

    def __str__(self) -> str:
        parts = ["QUIC handshake failed"]
        if self.tls_alert:
            parts.append(f"alert={self.tls_alert}")
        if self.tls_error:
            parts.append(f"tls_error={self.tls_error}")
        if self.reason:
            parts.append(f": {self.reason}")
        return " ".join(parts)


def quic_terminal_error(
    connection: Connection | None,
    error_code: int,
    reason: str,
) -> QuicConnectionError | None:
    """QUIC の終了から異常終了を表す例外を作る

    正常終了 (エラーコード 0・アイドルタイムアウト・ローカルからの close()) の
    場合は None を返す。ピアが CONNECTION_CLOSE を送った場合は、その種別に応じた
    例外 (`QuicTransportError` / `QuicApplicationError`) を返す。

    Args:
        connection: 終了した QUIC 接続 (ピアの CONNECTION_CLOSE を読む)
        error_code: 終了イベントのエラーコード (ローカル検知のエラーで 0 以外)
        reason: 終了イベントの理由

    Returns:
        異常終了を表す例外。正常終了の場合は None
    """
    if error_code != 0:
        # ローカルで検知したエラー (致命的エラーなど)。ピアの CONNECTION_CLOSE
        # ではないためトランスポートエラーとして扱う
        return QuicTransportError(error_code, reason, error_code_type="transport")
    if connection is not None and connection.error_code:
        return connection_error(connection, reason)
    return None


def connection_error(
    connection: Connection | None, default_reason: str = ""
) -> QuicConnectionError:
    """`quic.Connection` のエラー状態から例外を作る

    ピアが CONNECTION_CLOSE を送っている場合はそのエラーコード・理由・種別を、
    送っていない場合は `default_reason` を理由にした
    `QuicConnectionError` を使う。エラーコードの種別に応じて
    `QuicTransportError` / `QuicApplicationError` を選ぶ。

    Args:
        connection: エラー状態を読む QUIC 接続。None の場合は `default_reason`
            だけを使う
        default_reason: ピアが CONNECTION_CLOSE を送っていない場合の理由

    Returns:
        終了理由を表す例外
    """
    if connection is None:
        return QuicConnectionError(0, default_reason)
    error_code = connection.error_code
    if error_code is None:
        # ピアの CONNECTION_CLOSE は受信していない (ccerr の既定値は NO_ERROR)
        return QuicConnectionError(0, default_reason)
    reason = connection.reason or default_reason
    # フレーム種別 0 は ngtcp2 の「不明」を表す。0 は PADDING でもあるが、
    # エラーを引き起こしたフレームが PADDING になることはない
    frame_type = connection.error_frame_type or -1
    if connection.error_code_type is ConnectionErrorType.APPLICATION:
        return QuicApplicationError(
            error_code, reason, error_code_type="application", frame_type=frame_type
        )
    return QuicTransportError(
        error_code, reason, error_code_type="transport", frame_type=frame_type
    )
