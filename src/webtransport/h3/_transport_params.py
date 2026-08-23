"""WebTransport over HTTP/3 の transport parameter 検証ヘルパー"""

from __future__ import annotations

from webtransport import quic


def meets_transport_param_requirements(conn: quic.Connection) -> bool:
    """QUIC 接続の transport parameter が WebTransport の要件を満たすか確認する

    draft-ietf-webtrans-http3-16 Section 3.1 の MUST は
    max_datagram_frame_size > 0 と reset_stream_at の送信を要求するが、
    reset_stream_at は実ブラウザ (Chromium / WebKit) が現時点で送信しない
    ため、相互運用性を優先して必須としない (該当 MUST の将来の改訂で
    見直す可能性がある)。max_datagram_frame_size > 0 のみを必須とする。
    ハンドシェイク完了前 (transport parameter 未受信) は None になるため
    要件未達として扱う。クライアントとサーバーの双方で使用する
    """
    max_datagram_frame_size = conn.remote_max_datagram_frame_size
    return max_datagram_frame_size is not None and max_datagram_frame_size > 0
