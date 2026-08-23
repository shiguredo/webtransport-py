"""WebTransport over HTTP/3 の :protocol トークン検証テスト

draft-ietf-webtrans-http3-16 Section 3.2 は「:protocol は webtransport-h3
であること」を定めるが、実ブラウザ (Chromium / WebKit) および Shiguredo
WebTransport DevTools は現時点でも ":protocol: webtransport" で CONNECT する
ため、本実装は両方のトークンをネイティブ HTTP/3 セッションとして受理する。

- "webtransport" (カプセルベースプロトコル用トークン。draft-16 Section 2.1.2)
- "webtransport-h3" (ネイティブ HTTP/3 セッション用トークン。draft-16 Section
  3.2)
"""

from __future__ import annotations

from conftest import _create_session_pair, _drain_events, _setup_connect

from webtransport import h3


def _encode_qpack_literal_value(value: str) -> bytes:
    """QPACK の文字列リテラル (Huffman なし、RFC 9204 Section 4.1.2) を組み立てる"""
    data = value.encode()
    assert len(data) < 128
    return bytes([len(data)]) + data


def _encode_connect_headers(protocol: str) -> bytes:
    """CONNECT リクエストのヘッダーを QPACK 静的テーブル参照で手動エンコードする

    通常クライアントの connect() は :protocol を "webtransport-h3" に固定して
    送出するため、任意の :protocol を注入するには手動エンコードが必要
    (RFC 9204 Section 4.5)。

    エンコード内容 (全て静的テーブル参照):
        :method CONNECT   -> 静的 index 15 (indexed)
        :scheme https     -> 静的 index 23 (indexed)
        :authority        -> 静的 index 0 (name reference)
        :path             -> 静的 index 1 (name reference)
        :protocol         -> 静的テーブルに無いためリテラル名でエンコード
    """
    # Encoded Field Section Prefix (Required Insert Count=0, Base=0)
    # 動的テーブルを参照しないため静的のみで完結する (RFC 9204 Section 4.5.1)
    block = b"\x00\x00"
    block += bytes([0xC0 | 15])  # :method CONNECT
    block += bytes([0xC0 | 23])  # :scheme https
    block += bytes([0x50 | 0]) + _encode_qpack_literal_value("localhost")  # :authority
    block += bytes([0x50 | 1]) + _encode_qpack_literal_value("/webtransport")  # :path
    # :protocol は名前長 9 が 3bit prefix (最大 7) に収まらないため、
    # prefixed integer で 7 + 継続バイト 2 に分割する
    block += bytes([0x20 | 7]) + bytes([2]) + b":protocol"
    block += _encode_qpack_literal_value(protocol)
    return bytes([0x01, len(block)]) + block  # HEADERS フレームでラップ


def _create_injectable_pair() -> tuple[h3.Session, h3.Session]:
    """QPACK エンコーダーストリーム転送済みのセッションペアを作成する

    client.connect() で QPACK エンコーダーストリームのデータを生成し、
    制御・QPACK ストリームをサーバーへ渡す (_setup_connect はデータが
    無くなるまでループで取り出す)。CONNECT ストリーム (0) は手動エンコード
    したヘッダーを注入するため渡さない。

    @return (クライアント Session, サーバー Session)
    """
    client, server = _create_session_pair()
    assert client.connect(0, "https://localhost/webtransport") is True
    _setup_connect(client, server, 0)
    return client, server


def _inject_protocol_connect(
    server: h3.Session,
    protocol: str,
) -> None:
    """指定した :protocol の CONNECT ヘッダーをサーバーへ注入する"""
    ret = server.receive_stream_data(0, _encode_connect_headers(protocol), False)
    assert ret > 0, "CONNECT ヘッダーの注入に失敗しました"


def test_connect_protocol_webtransport_accepted() -> None:
    """:protocol: webtransport の CONNECT が受理されることを確認

    draft-ietf-webtrans-http3-16 Section 2.1.2 では "webtransport" はカプセル
    ベースプロトコル用トークンであり、Section 3.2 の「:protocol は
    webtransport-h3 であること」に反する。しかし実ブラウザ (Chromium /
    WebKit) および Shiguredo WebTransport DevTools は現時点でも
    ":protocol: webtransport" で CONNECT する (実測済み) ため、
    本実装ではネイティブ HTTP/3 セッションとして受理する (互換のための
    仕様からの既知の逸脱。将来の実ブラウザの移行後は 501 拒否に戻すべき)。
    """
    _, server = _create_injectable_pair()

    # ":protocol: webtransport" の CONNECT を注入する
    _inject_protocol_connect(server, "webtransport")

    # ネイティブセッションとして受理される (SESSION_READY 発火・登録)
    events = _drain_events(server)
    assert any(event.type == h3.EventType.SESSION_READY for event in events)
    assert server.get_session_ids() == [0]


def test_connect_protocol_webtransport_client_session_kept() -> None:
    """受理後のクライアント側セッションが維持されることを確認

    "webtransport" の CONNECT がネイティブセッションとして受理されると、
    クライアントの session_ids_ に登録されたままになる (受理は 2xx 応答で
    成立する)。拒否された場合のセッション削除は起こらない。
    """
    client, server = _create_injectable_pair()

    # ":protocol: webtransport" の CONNECT を注入する
    _inject_protocol_connect(server, "webtransport")

    # サーバー側でセッションが受理される (SESSION_READY 発火)
    ready_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1
    assert server.accept_session(0) is True

    # 2xx 応答を SESSION へ戻す
    server_responses = []
    for _ in range(64):
        streams = server.get_streams_to_send()
        if not streams:
            break
        for stream_id, data, fin in streams:
            if stream_id == 0:
                server_responses.append((stream_id, data, fin))
            else:
                client.receive_stream_data(stream_id, data, fin)
    assert len(server_responses) == 1
    client.receive_stream_data(0, server_responses[0][1], server_responses[0][2])

    # クライアントのセッションが維持される (2xx 受理の意味論)
    assert client.get_session_ids() == [0]
    # 受理されたセッションへの送信は塞がれない
    client.send_datagram(0, b"after-accept")
    assert len(client.get_datagrams_to_send()) > 0


def test_connect_protocol_webtransport_h3_accepted() -> None:
    """正しいトークン (webtransport-h3) の CONNECT は受理されることを確認

    手動エンコードした ":protocol: webtransport-h3" の CONNECT が通常経路と
    同じく受理されることの回帰確認。
    """
    _, server = _create_injectable_pair()

    # ":protocol: webtransport-h3" の CONNECT を注入する
    _inject_protocol_connect(server, "webtransport-h3")

    # SESSION_READY が発火してセッションが登録される
    ready_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1
    assert ready_events[0].session_id == 0
    assert server.get_session_ids() == [0]
