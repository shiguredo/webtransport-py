"""層ごとの例外階層の検証

例外クラスの基底関係・エラーコード・属性の持ち方を検証する。値は RFC と
draft の定義を直接照合し、`str()` がエラーコード名と理由を含むこと、
`quic.exceptions.connection_error` がピアの CONNECTION_CLOSE の有無で
種別を切り替えることを確認する。モックは使わず、実際のバインディングの
オブジェクトだけを使う。
"""

from __future__ import annotations

import pytest

from webtransport import h2, h3, http2, http3, quic
from webtransport.exceptions import (
    ConnectFailedError,
    ConnectTimeoutError,
    WebTransportError,
)
from webtransport.h2 import exceptions as h2_exceptions
from webtransport.h3 import exceptions as h3_exceptions
from webtransport.http2 import exceptions as http2_exceptions
from webtransport.http3 import exceptions as http3_exceptions
from webtransport.quic import exceptions as quic_exceptions


def test_exception_modules_are_importable_from_layers() -> None:
    """層のパッケージから exceptions サブモジュールを参照できる"""
    assert quic.exceptions is quic_exceptions
    assert http2.exceptions is http2_exceptions
    assert http3.exceptions is http3_exceptions
    assert h2.exceptions is h2_exceptions
    assert h3.exceptions is h3_exceptions


@pytest.mark.parametrize(
    "exception_class",
    [
        quic_exceptions.QuicError,
        quic_exceptions.QuicConnectionError,
        quic_exceptions.QuicTransportError,
        quic_exceptions.QuicApplicationError,
        quic_exceptions.QuicHandshakeError,
        http2_exceptions.Http2Error,
        http2_exceptions.Http2ConnectionError,
        http2_exceptions.Http2HandshakeError,
        http3_exceptions.Http3Error,
        http3_exceptions.Http3ConnectionError,
        h2_exceptions.WebTransportH2Error,
        h2_exceptions.WebTransportSessionRejectedError,
        h2_exceptions.WebTransportSessionClosedError,
        h2_exceptions.WebTransportProtocolError,
        h3_exceptions.WebTransportH3Error,
        h3_exceptions.WebTransportSessionRejectedError,
        h3_exceptions.WebTransportSessionClosedError,
        h3_exceptions.WebTransportProtocolError,
        ConnectFailedError,
        ConnectTimeoutError,
    ],
)
def test_all_exceptions_derive_from_webtransport_error(exception_class: type) -> None:
    """すべての例外が WebTransportError の派生である"""
    assert issubclass(exception_class, WebTransportError)


def test_quic_connection_error_subclasses() -> None:
    """接続終了の例外が接続エラーの派生になっている"""
    assert issubclass(quic_exceptions.QuicTransportError, quic_exceptions.QuicConnectionError)
    assert issubclass(quic_exceptions.QuicApplicationError, quic_exceptions.QuicConnectionError)
    assert not issubclass(quic_exceptions.QuicHandshakeError, quic_exceptions.QuicConnectionError)


@pytest.mark.parametrize(
    ("error_code", "expected"),
    [
        # RFC 9000 Section 20.1
        (quic_exceptions.QuicTransportErrorCode.NO_ERROR, 0x00),
        (quic_exceptions.QuicTransportErrorCode.FLOW_CONTROL_ERROR, 0x03),
        (quic_exceptions.QuicTransportErrorCode.PROTOCOL_VIOLATION, 0x0A),
        (quic_exceptions.QuicTransportErrorCode.CRYPTO_BUFFER_EXCEEDED, 0x0D),
        (quic_exceptions.QuicTransportErrorCode.NO_VIABLE_PATH, 0x10),
        # RFC 9368 Section 4
        (quic_exceptions.QuicTransportErrorCode.VERSION_NEGOTIATION_ERROR, 0x11),
        # RFC 9001 Section 4.8 (CRYPTO_ERROR 範囲)
        (quic_exceptions.QuicCryptoErrorCode.CRYPTO_ERROR, 0x0100),
        (quic_exceptions.QuicCryptoErrorCode.HANDSHAKE_FAILURE, 0x0128),
        (quic_exceptions.QuicCryptoErrorCode.NO_APPLICATION_PROTOCOL, 0x0178),
        # RFC 9113 Section 7
        (http2_exceptions.Http2ErrorCode.REFUSED_STREAM, 0x07),
        (http2_exceptions.Http2ErrorCode.ENHANCE_YOUR_CALM, 0x0B),
        (http2_exceptions.Http2ErrorCode.HTTP_1_1_REQUIRED, 0x0D),
        # RFC 9114 Section 8.1 / RFC 9204 Section 8.3
        (http3_exceptions.Http3ErrorCode.NO_ERROR, 0x0100),
        (http3_exceptions.Http3ErrorCode.GENERAL_PROTOCOL_ERROR, 0x0101),
        (http3_exceptions.Http3ErrorCode.MISSING_SETTINGS, 0x010A),
        (http3_exceptions.Http3ErrorCode.QPACK_DECOMPRESSION_FAILED, 0x0200),
        # draft-ietf-webtrans-http3-16 Section 9.5
        (http3_exceptions.Http3ErrorCode.WT_FLOW_CONTROL_ERROR, 0x045D4487),
        (http3_exceptions.Http3ErrorCode.WT_SESSION_GONE, 0x170D7B68),
        (http3_exceptions.Http3ErrorCode.WT_BUFFERED_STREAM_REJECTED, 0x3994BD84),
        (h3_exceptions.WebTransportErrorCode.WT_FLOW_CONTROL_ERROR, 0x045D4487),
        (h3_exceptions.WebTransportErrorCode.WT_SESSION_GONE, 0x170D7B68),
        (h3_exceptions.WebTransportErrorCode.WT_BUFFERED_STREAM_REJECTED, 0x3994BD84),
        # draft-ietf-webtrans-http2-15 Section 3.4 (draft は 0xTBD のため
        # 本ライブラリ固定値。低レベルの h2.WtErrorCode と一致させる)
        (h2_exceptions.WebTransportErrorCode.WT_FLOW_CONTROL_ERROR, 0x50),
        (h2_exceptions.WebTransportErrorCode.WT_STREAM_STATE_ERROR, 0x51),
        (h2_exceptions.WebTransportErrorCode.WT_ERROR, 0x52),
    ],
)
def test_error_code_values_match_specifications(error_code: int, expected: int) -> None:
    """エラーコードの値が仕様どおりである"""
    assert int(error_code) == expected


def h2_low_member(name: str) -> int:
    """低レベルの h2.WtErrorCode から同じ名前の値取り出す"""
    from webtransport.webtransport_ext.h2 import WtErrorCode

    return int(WtErrorCode[name].value)


def test_h2_error_codes_match_low_level_enum() -> None:
    """WebTransport over HTTP/2 のエラーコードが低レベルの enum と一致する"""
    for name, member in h2_exceptions.WebTransportErrorCode.__members__.items():
        assert h2_low_member(name) == int(member)


def test_quic_connection_error_attributes() -> None:
    """QUIC の接続エラーがエラーコード・理由・種別・フレーム種別を持つ"""
    error = quic_exceptions.QuicTransportError(
        0x0A, "frame violates protocol", error_code_type="transport", frame_type=0x1C
    )
    assert error.error_code == 0x0A
    assert error.reason == "frame violates protocol"
    assert error.error_code_type == "transport"
    assert error.frame_type == 0x1C
    # 既定はローカル検知 (エラーコードなし・フレーム不明)
    default = quic_exceptions.QuicConnectionError()
    assert default.error_code == 0
    assert default.error_code_type == "local"
    assert default.frame_type == -1


def test_str_contains_error_code_and_reason() -> None:
    """例外の文字列がエラーコード名と理由を含む"""
    transport = quic_exceptions.QuicTransportError(0x03, "flow control violated")
    assert "FLOW_CONTROL_ERROR" in str(transport)
    assert "0x3" in str(transport)
    assert "flow control violated" in str(transport)

    unknown = quic_exceptions.QuicTransportError(0x7F, "")
    assert "0x7f" in str(unknown)

    closed = h3_exceptions.WebTransportSessionClosedError(0x170D7B68, "session gone")
    assert "0x170d7b68" in str(closed)
    assert "session gone" in str(closed)

    rejected = h2_exceptions.WebTransportSessionRejectedError(403)
    assert "403" in str(rejected)


def test_handshake_error_attributes() -> None:
    """TLS ハンドシェイクの例外が TLS エラーとアラートを持つ"""
    error = quic_exceptions.QuicHandshakeError(
        "certificate verify failed", tls_error=1, tls_alert=42
    )
    assert error.reason == "certificate verify failed"
    assert error.tls_error == 1
    assert error.tls_alert == 42
    assert "alert=42" in str(error)


def test_connection_error_falls_back_to_local_without_peer_close() -> None:
    """ピアの CONNECTION_CLOSE が無い接続ではローカル終了として扱う

    接続をまだ確立していない実物の `quic.Connection` を使う (モックは使わない)。
    """
    config = quic.Config()
    connection = quic.Connection.create_client(config, ("127.0.0.1", 0), ("127.0.0.1", 4433))
    error = quic_exceptions.connection_error(connection, "connection draining")
    assert isinstance(error, quic_exceptions.QuicConnectionError)
    # ピアの CONNECTION_CLOSE を受信していないため、エラーコードは 0 になる
    assert error.error_code == 0
    assert error.error_code_type == "local"
    assert "connection draining" in str(error)


def test_connection_error_without_connection() -> None:
    """接続が無い場合は理由だけを持つ例外になる"""
    error = quic_exceptions.connection_error(None, "connection closing")
    assert error.error_code == 0
    assert error.reason == "connection closing"


def test_quic_terminal_error_returns_none_for_clean_end() -> None:
    """正常終了 (エラーコード 0・ピアのクローズ無し) は例外にしない"""
    config = quic.Config()
    connection = quic.Connection.create_client(config, ("127.0.0.1", 0), ("127.0.0.1", 4433))
    assert quic_exceptions.quic_terminal_error(connection, 0, "idle timeout") is None


def test_quic_terminal_error_for_local_error() -> None:
    """ローカルで検知したエラーはトランスポートエラーの例外になる"""
    error = quic_exceptions.quic_terminal_error(None, 0x01, "fatal error: internal")
    assert isinstance(error, quic_exceptions.QuicTransportError)
    assert error.error_code == 0x01
    assert error.error_code_type == "transport"
