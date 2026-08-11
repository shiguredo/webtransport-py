"""WebTransport over HTTP/3 の open_stream セッション終了後拒否テスト

close_session (WT_CLOSE_SESSION 送出) と recv_wt_close_session_cb
(WT_CLOSE_SESSION 受信) は session_ids_ からセッション ID を削除するが、
nghttp3 の CONNECT ストリームはストリームテーブルに残存し wt.session も
解放されないため、終了したセッション ID 宛の open_stream が成功し得る。
この open_stream を拒否すること (draft-ietf-webtrans-http3-16 Section 6
の MUST「セッション終了を学習したエンドポイントは、新しいストリームも
開いてはならない」) を検証する。close_session 送出後の拒否は実効的な
回帰テストであり、WT_CLOSE_SESSION 受信後の経路は現在の依存 nghttp3 でも
nghttp3 側が拒否するため nghttp3 更新時のリグレッション防御として機能する。
"""

from __future__ import annotations

from conftest import _create_session_pair, _establish_session, _pump


def test_open_stream_after_sending_close_session_fails() -> None:
    """close_session 送出後の open_stream が失敗することを確認

    close_session を呼ぶと session_ids_ から削除されるため、そのセッション
    ID 宛の open_stream は false を返す (WT_CLOSE_SESSION カプセルの送出は
    get_streams_to_send による。送出後も拒否されることを併せて確認する)。
    """
    client, _server, session_id = _establish_session()

    # クライアントが close_session を呼び出す (送出前)
    client.close_session(session_id, 0)
    assert client.get_session_ids() == []

    # 終了したセッション ID 宛の open_stream は失敗する (送出前)
    assert client.open_stream(session_id, 4, False) is False

    # WT_CLOSE_SESSION の送出後も同様に失敗する
    client.get_streams_to_send()
    assert client.open_stream(session_id, 4, False) is False


def test_open_stream_after_recv_wt_close_session_fails() -> None:
    """WT_CLOSE_SESSION 受信後の open_stream が失敗することを確認

    サーバーが WT_CLOSE_SESSION を受信してセッション終了を学習した後、
    そのセッション ID 宛の open_stream は false を返す。
    """
    client, server, session_id = _establish_session()

    # クライアントが WT_CLOSE_SESSION を送出し、サーバーが受信する
    client.close_session(session_id, 0)
    _pump(client, server)
    assert server.get_session_ids() == []

    # 終了したセッション ID 宛の open_stream は失敗する
    # (ストリーム ID は制御ストリーム (3) / QPACK (7, 11) と重複しない
    # サーバー起動単方向 %4==3 を使う)
    assert server.open_stream(session_id, 15, True) is False


def test_open_stream_alive_session_succeeds() -> None:
    """生存セッションの open_stream は従来どおり成功することを確認

    session_ids_ に存在するセッション ID の open_stream は、終了した
    セッションの拒否の影響を受けずに成功する。
    """
    client, server, session_id = _establish_session()

    # サーバー側で単方向ストリームを開く (サーバー起動単方向 %4==3。
    # 制御ストリーム (3) と QPACK ストリーム (7, 11) と重複しない ID)
    stream_id = 15
    assert server.open_stream(session_id, stream_id, True) is True

    # クライアント側で双方向ストリームを開く (クライアント起動双方向 %4==0)
    assert client.open_stream(session_id, 4, False) is True


def test_open_stream_optimistic_succeeds() -> None:
    """セッション確立前の楽観的オープンが維持されることを確認

    draft-ietf-webtrans-http3-16 Section 4 の楽観的オープン: クライアントは
    CONNECT リクエスト送信後・サーバー応答前にストリームを開ける。connect
    直後に session_ids_ へ挿入されるため、メンバーシップ確認を通過する。
    """
    client, _server = _create_session_pair()

    # CONNECT リクエストを送信する (サーバー応答はまだ)
    assert client.connect(0, "https://localhost/webtransport") is True

    # サーバー応答前でも open_stream は成功する
    assert client.open_stream(0, 4, False) is True


def test_open_stream_after_connect_stream_close_fails() -> None:
    """CONNECT ストリームのクローズ経路では open_stream が失敗することを確認

    close_stream による CONNECT ストリームのクローズで終了したセッション
    ID 宛の open_stream は失敗する (既に正しく動作する経路の回帰確認)。
    クライアント側は close_stream を未学習のため成功するのが正しい挙動。
    """
    client, server, session_id = _establish_session()

    # CONNECT ストリームをクローズしてセッションを終了する (サーバー側)
    server.close_stream(session_id, 0)
    assert server.get_session_ids() == []

    # 終了したセッション ID 宛の open_stream は失敗する (サーバー側)
    assert server.open_stream(session_id, 15, True) is False

    # クライアント側はセッション終了を未学習のため成功する (正しい挙動)
    assert client.open_stream(session_id, 4, False) is True


def test_open_stream_unestablished_session_fails() -> None:
    """一度も確立されていないセッション ID の open_stream が失敗することを確認

    session_ids_ に含まれないセッション ID の open_stream は false を返す
    (本ライブラリの意味論の明文化。修正前から nghttp3 が拒否していた既存
    挙動)。
    """
    client, server, session_id = _establish_session()

    # 確立済み ID とは異なる、一度も確立されていない ID では失敗する
    unestablished_session_id = session_id + 4
    assert client.open_stream(unestablished_session_id, 4, False) is False
    assert server.open_stream(unestablished_session_id, 15, True) is False
