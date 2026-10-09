"""QMux の例外

QMux 層で起きた失敗を表す。dwnx のライブラリエラーコードは `library_error_name`
で名前に、`Connection.strerror` で dwnx 自身のメッセージに変換できる。
"""

import enum

from webtransport.exceptions import WebTransportError

__all__ = [
    "QmuxConnectionError",
    "QmuxError",
    "QmuxLibraryErrorCode",
    "QmuxProtocolError",
    "is_graceful_termination",
    "library_error_name",
]


class QmuxError(WebTransportError):
    """QMux 層の基底例外"""


class QmuxConnectionError(QmuxError):
    """トランスポートの接続と切断の失敗 (TCP の切断、TLS の失敗など)"""


class QmuxProtocolError(QmuxError):
    """QMux のプロトコル違反、または dwnx のライブラリエラー"""


class QmuxLibraryErrorCode(enum.IntEnum):
    """dwnx のライブラリエラーコード (dwnx.h の DWNX_ERR_*)

    接続が終了することを表すコード (CLOSING / DRAINING / IDLE_CLOSE) は正常な
    終了経路でも返るため、`is_graceful_termination` で区別する。
    """

    INVALID_ARGUMENT = -201
    NOBUF = -202
    PROTO = -203
    INVALID_STATE = -204
    STREAM_ID_BLOCKED = -206
    STREAM_IN_USE = -207
    STREAM_DATA_BLOCKED = -208
    FLOW_CONTROL = -209
    STREAM_LIMIT = -211
    FINAL_SIZE = -212
    REQUIRED_TRANSPORT_PARAM = -215
    MALFORMED_TRANSPORT_PARAM = -216
    FRAME_ENCODING = -217
    STREAM_SHUT_WR = -219
    STREAM_NOT_FOUND = -220
    STREAM_STATE = -221
    CLOSING = -223
    DRAINING = -224
    TRANSPORT_PARAM = -225
    INTERNAL = -228
    WRITE_MORE = -230
    IDLE_CLOSE = -238


# 接続が終了したことを表すコード。正常な終了経路でも返るため例外にしない
_GRACEFUL_CODES = frozenset(
    {
        int(QmuxLibraryErrorCode.CLOSING),
        int(QmuxLibraryErrorCode.DRAINING),
        int(QmuxLibraryErrorCode.IDLE_CLOSE),
    }
)


def library_error_name(code: int) -> str:
    """dwnx のライブラリエラーコードを名前に変換する

    Args:
        code: dwnx が返した負値

    Returns:
        名前 (`DRAINING` など)。未知のコードは `DWNX_ERR_<code>` になる
    """
    try:
        return QmuxLibraryErrorCode(code).name
    except ValueError:
        return f"DWNX_ERR_{code}"


def is_graceful_termination(code: int) -> bool:
    """接続の終了を表すコードかどうかを返す

    Args:
        code: dwnx が返した負値

    Returns:
        正常な終了経路 (ピアの CONNECTION_CLOSE 後の draining など) なら True
    """
    return code in _GRACEFUL_CODES
