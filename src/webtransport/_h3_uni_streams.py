"""HTTP/3 の制御・QPACK 単方向ストリームの開設とバインド

3 本は HTTP/3 の接続確立時に必ず開く必要があり、開設とバインドの両方を
ここに集約する。失敗時の扱いは層ごとに異なるため呼び出し側に残す。
"""

from typing import Any


def open_http3_uni_streams(quic_connection: Any) -> tuple[int, int, int]:
    """HTTP/3 の制御・QPACK エンコーダ・QPACK デコーダの単方向ストリームを開く

    3 本は常にこの順で開き、同じ順で bind する必要がある (RFC 9114 Section
    6.2)。開設自体はここに集約し、bind と失敗時の扱いは呼び出し側に残す
    (クライアントは開設失敗を無視し、サーバーは制御ストリームの失敗で打ち
    切るなど、層ごとに扱いが異なるため)。

    Args:
        quic_connection: ストリームを開く QUIC コネクション

    Returns:
        (制御, QPACK エンコーダ, QPACK デコーダ) のストリーム ID
    """
    control = quic_connection.open_stream(False)
    encoder = quic_connection.open_stream(False)
    decoder = quic_connection.open_stream(False)
    return control, encoder, decoder


def bind_http3_uni_streams(
    h3_connection: Any,
    control_stream_id: int,
    qpack_encoder_stream_id: int,
    qpack_decoder_stream_id: int,
) -> None:
    """HTTP/3 の制御・QPACK ストリームをバインドする

    Args:
        h3_connection: bind を受け持つ HTTP/3 または WebTransport セッション
        control_stream_id: 制御ストリーム ID
        qpack_encoder_stream_id: QPACK エンコーダストリーム ID
        qpack_decoder_stream_id: QPACK デコーダストリーム ID
    """
    h3_connection.bind_control_stream(control_stream_id)
    h3_connection.bind_qpack_encoder_stream(qpack_encoder_stream_id)
    h3_connection.bind_qpack_decoder_stream(qpack_decoder_stream_id)
