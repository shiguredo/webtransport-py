"""WebTransport over HTTP/3 の transport parameter 検証ヘルパー"""

from __future__ import annotations

from webtransport import quic


def meets_transport_param_requirements(conn: quic.Connection) -> bool:
    """QUIC 接続の transport parameter が WebTransport の要件を満たすか確認する

    draft-ietf-webtrans-http3-16 Section 3.1 の MUST:
    max_datagram_frame_size > 0 と reset_stream_at の送信。
    ハンドシェイク完了前 (transport parameter 未受信) は None になるため
    要件未達として扱う。クライアントとサーバーの双方で使用する
    """
    max_datagram_frame_size = conn.remote_max_datagram_frame_size
    if max_datagram_frame_size is None or max_datagram_frame_size == 0:
        return False

    reset_stream_at = conn.remote_reset_stream_at
    return reset_stream_at is True
