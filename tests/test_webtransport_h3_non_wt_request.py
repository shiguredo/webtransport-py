"""WebTransport over HTTP/3 の非 WebTransport リクエストへの 405 応答テスト

draft-ietf-webtrans-http3-16 Section 3.2 の 405 SHOULD は extended CONNECT +
:protocol=webtransport-h3 が対象だが、WebTransport 専用エンドポイントとして
通常の HTTP リクエストにも 405 を返す (h2 のサーバーと同じ実装ポリシー)。
RFC 9110 Section 15.5.6 の MUST に従い、405 には Allow: CONNECT を付ける。
応答ヘッダーの観測には http3.Connection クライアントを使う (h3.Session の
Event には headers がないため)。
"""

from __future__ import annotations

import pytest
from conftest import _create_h3_http3_pair, _drain_events, _h3_http3_pump

from webtransport import h3, http3

# リクエストと応答に使うクライアント起動双方向ストリーム
_STREAM_ID = 0


def _base_headers() -> list[tuple[str, str]]:
    """非 WebTransport の GET および extended CONNECT の共通ヘッダーを組み立てる"""
    return [
        (":scheme", "https"),
        (":authority", "localhost"),
        (":path", "/webtransport"),
    ]


def _wt_connect_headers() -> list[tuple[str, str]]:
    """WebTransport の extended CONNECT のヘッダーを組み立てる"""
    return [
        (":method", "CONNECT"),
        (":protocol", "webtransport-h3"),
        *_base_headers(),
    ]


def _response_headers(
    client: http3.Connection, stream_id: int
) -> tuple[dict[str, str], list[http3.Event]]:
    """指定ストリームで受信した HEADERS の応答ヘッダーと全イベントを返す

    応答は 1 つの HEADERS イベントで届くため、複数あればテストの前提が
    崩れている。呼び出し側で STREAM_END を表明する。
    """
    events = _drain_events(client)
    headers_events = [
        e for e in events if e.type == http3.EventType.HEADERS and e.stream_id == stream_id
    ]
    assert len(headers_events) == 1
    return dict(headers_events[0].headers), events


@pytest.mark.parametrize(
    "headers",
    [
        [
            (":method", "GET"),
            *_base_headers(),
        ],
        [
            (":method", "CONNECT"),
            (":authority", "localhost"),
        ],
        [
            (":method", "CONNECT"),
            (":protocol", "websocket"),
            *_base_headers(),
        ],
    ],
    ids=["get", "connect_without_protocol", "connect_with_other_protocol"],
)
def test_non_wt_request_rejected_with_405_and_allow(headers: list[tuple[str, str]]) -> None:
    """非 WebTransport リクエストに 405 と allow: CONNECT が返ることを確認

    修正前の実装は非 WebTransport リクエストを無応答で破棄していた。405 応答で
    ストリームが終端されることを HEADERS と STREAM_END で表明する。判定は
    is_connect と is_webtransport の論理積であり、GET・:authority のみの古典
    CONNECT・他プロトコル CONNECT は 405 になる (:scheme / :path を伴う
    :protocol なし CONNECT は nghttp3 が ERR_MALFORMED_HTTP_HEADER として接続
    エラーにするため対象外)。

    未対応 :protocol の extended CONNECT には RFC 9220 Section 3 の 501 SHOULD
    があるが、WebTransport 専用エンドポイントとして h2 と揃えて 405 を返す。
    """
    client, server = _create_h3_http3_pair()

    assert client.submit_request(_STREAM_ID, headers)
    _h3_http3_pump(client, server)

    response_headers, events = _response_headers(client, _STREAM_ID)
    assert response_headers[":status"] == "405"
    assert response_headers["allow"] == "CONNECT"
    # 応答ヘッダーは :status と allow のみで、応答でストリームが終端される
    assert set(response_headers) == {":status", "allow"}
    assert any(e.type == http3.EventType.STREAM_END and e.stream_id == _STREAM_ID for e in events)
    # 非 WebTransport リクエストでサーバー側にエラーは発生しない
    assert all(e.type != h3.EventType.ERROR for e in _drain_events(server))


@pytest.mark.parametrize(
    "status_code, expected_allow",
    [(405, "CONNECT"), (403, None), (302, None), (500, None)],
    ids=["method_not_allowed", "forbidden", "redirect", "server_error"],
)
def test_reject_session_allow_header_only_for_405(
    status_code: int, expected_allow: str | None
) -> None:
    """reject_session の応答の allow: CONNECT が 405 のときのみ付くことを確認

    RFC 9110 Section 15.5.6 は 405 応答に Allow ヘッダーを含めることを
    MUST とする。WebTransport の extended CONNECT を受理前のサーバーが
    拒否する経路で表明する。405 以外では allow を付けない。
    """
    client, server = _create_h3_http3_pair()

    assert client.submit_request(_STREAM_ID, _wt_connect_headers())
    _h3_http3_pump(client, server)

    ready_events = [e for e in _drain_events(server) if e.type == h3.EventType.SESSION_READY]
    assert len(ready_events) == 1
    session_id = ready_events[0].session_id

    server.reject_session(session_id, status_code)
    _h3_http3_pump(client, server)

    response_headers, events = _response_headers(client, session_id)
    assert response_headers[":status"] == str(status_code)
    if expected_allow is None:
        # 405 以外では Allow ヘッダーを付けない
        assert "allow" not in response_headers
    else:
        assert response_headers["allow"] == expected_allow
    # 拒否されたセッションは確立されず、応答でストリームが終端される
    assert any(e.type == http3.EventType.STREAM_END and e.stream_id == session_id for e in events)
    # 拒否経路でサーバー側にエラーは発生しない
    assert all(e.type != h3.EventType.ERROR for e in _drain_events(server))


def test_non_wt_request_does_not_break_accepted_session() -> None:
    """受理済みセッションがある状態でも非 WT リクエストの 405 がセッションを壊さないことを確認

    405 は非 WebTransport リクエストのストリームにのみ作用し、受理済み
    セッション (session_ids_) を削除しない。
    """
    client, server = _create_h3_http3_pair()

    # WT CONNECT を送って受理する
    assert client.submit_request(_STREAM_ID, _wt_connect_headers())
    _h3_http3_pump(client, server)
    ready_events = [e for e in _drain_events(server) if e.type == h3.EventType.SESSION_READY]
    assert len(ready_events) == 1
    session_id = ready_events[0].session_id
    assert server.accept_session(session_id) is True
    _h3_http3_pump(client, server)

    # 受理応答 (2xx) をクライアントで観測する
    accept_headers = [e for e in _drain_events(client) if e.type == http3.EventType.HEADERS]
    assert len(accept_headers) == 1
    assert dict(accept_headers[0].headers)[":status"] == "200"
    assert server.get_session_ids() == [session_id]

    # 別ストリーム (4) の非 WT リクエストは 405 になり、セッションは残る
    assert client.submit_request(4, [(":method", "GET"), *_base_headers()])
    _h3_http3_pump(client, server)
    response_headers, events = _response_headers(client, 4)
    assert response_headers[":status"] == "405"
    assert response_headers["allow"] == "CONNECT"
    assert any(e.type == http3.EventType.STREAM_END and e.stream_id == 4 for e in events)
    assert server.get_session_ids() == [session_id]
    # 405 の処理でサーバー側にエラーは発生しない
    assert all(e.type != h3.EventType.ERROR for e in _drain_events(server))
