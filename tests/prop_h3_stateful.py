"""WebTransport over HTTP/3 の確立済みペアに対するステートフル PBT

既存の `prop_*.py` は「新規未接続オブジェクト 1 個」または「1 回の
ハンドシェイク」に対する操作しか検証しておらず、確立済みペアへの API
呼び出し系列を駆動する property test が無かった。そのため issue 0145 の
QPACK ブロック中 DATA pipeline による SIGABRT (3 ステップの状態遷移で
発火) を 136 個の property test が見逃した。本ファイルは
`RuleBasedStateMachine` で呼び出し系列を生成し、abort しないことと
不変条件を検証する回帰ピンである。
"""

from __future__ import annotations

from conftest import _drain_events, _pump
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from webtransport import h3

# 確立済みペアを作るための固定値 (クライアント起動双方向ストリーム 0 を
# CONNECT に使う)
CONNECT_STREAM_ID = 0
# WT データストリームに使うクライアント起動双方向ストリーム (GET_REQUIRED_STREAMS
# と同じ 4 の倍数)
DATA_STREAM_IDS = (4, 8, 12)
# データグラムのペイロード上限 (draft-16 Section 4.5 の 1200 バイト目安)
MAX_DATAGRAM_SIZE = 1200


def _create_established_pair() -> tuple[h3.Session, h3.Session, int]:
    """確立済みの h3 クライアント・サーバーペアを作る

    `conftest._establish_session` と同じ手順を、この module 内で完結する
    形で持つ (stateful PBT はテストごとに状態を作り直すため)。
    """
    client = h3.Session.create_client(h3.Config())
    server_config = h3.Config()
    server_config.is_server = True
    server = h3.Session.create_server(server_config)

    client.bind_control_stream(2)
    client.bind_qpack_encoder_stream(6)
    client.bind_qpack_decoder_stream(10)
    server.bind_control_stream(3)
    server.bind_qpack_encoder_stream(7)
    server.bind_qpack_decoder_stream(11)
    server.set_max_client_streams_bidi(100)

    # サーバーの SETTINGS をクライアントへ
    _pump(server, client)

    assert client.connect(CONNECT_STREAM_ID, "https://localhost/webtransport") is True
    _pump(client, server)

    # サーバー側で SESSION_READY を処理して受理する
    ready_events = [
        event for event in _drain_events(server) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1, "SESSION_READY が 1 回だけ発火するべき"
    session_id = ready_events[0].session_id
    assert server.accept_session(session_id) is True

    _pump(server, client)
    ready_events = [
        event for event in _drain_events(client) if event.type == h3.EventType.SESSION_READY
    ]
    assert len(ready_events) == 1, "クライアント側でも SESSION_READY が 1 回発火するべき"

    return client, server, session_id


class H3EstablishedPairMachine(RuleBasedStateMachine):
    """確立済み h3 ペアへの API 呼び出し系列を駆動する状態機械

    client / server のどちらかへの操作と、送信キューを相手へ渡す pump を
    組み合わせて系列を作る。各 rule は abort しないこと、invariant は
    セッション ID の整合とイベントの重複発火が無いことを検証する。
    """

    def __init__(self) -> None:
        super().__init__()
        self.client, self.server, self.session_id = _create_established_pair()
        # 開いたストリーム ID (client 起動 bidi)
        self.opened_streams: list[int] = []
        # 受信したデータグラム
        self.received_datagrams: list[bytes] = []
        # client が送出した累計バイト数
        self.client_sent_bytes = 0
        # SessionClosed の発火回数 (side ごとに 0 または 1)
        self.client_closed_count = 0
        self.server_closed_count = 0

    # ========== rule ==========

    @rule(stream_id=st.sampled_from(DATA_STREAM_IDS))
    def open_stream(self, stream_id: int) -> None:
        """クライアントが双方向ストリームを開く"""
        if stream_id in self.opened_streams:
            return
        opened = self.client.open_stream(self.session_id, stream_id, False)
        if opened:
            self.opened_streams.append(stream_id)

    @rule(
        data=st.binary(max_size=512),
        fin=st.booleans(),
        stream_id=st.sampled_from(DATA_STREAM_IDS),
    )
    def send_stream_data(self, stream_id: int, data: bytes, fin: bool) -> None:
        """開いているストリームへデータを送る (開いていなければ無視される)"""
        self.client.send_stream_data(stream_id, data, fin)
        self.client_sent_bytes += len(data)

    @rule(
        error_code=st.integers(min_value=0, max_value=0xFFFFFFFF),
        stream_id=st.sampled_from(DATA_STREAM_IDS),
    )
    def reset_stream(self, stream_id: int, error_code: int) -> None:
        """ストリームをリセットする"""
        self.client.reset_stream(stream_id, error_code)

    @rule(data=st.binary(max_size=MAX_DATAGRAM_SIZE))
    def send_datagram(self, data: bytes) -> None:
        """データグラムを送る"""
        self.client.send_datagram(self.session_id, data)

    @rule()
    def pump_client_to_server(self) -> None:
        """クライアントの送信キューをサーバーへ渡す"""
        _pump(self.client, self.server)
        self._observe_server_events()

    @rule()
    def pump_server_to_client(self) -> None:
        """サーバーの送信キューをクライアントへ渡す"""
        _pump(self.server, self.client)
        self._observe_client_events()

    @rule(error_code=st.integers(min_value=0, max_value=0xFFFFFFFF))
    def close_session(self, error_code: int) -> None:
        """クライアントがセッションを閉じる"""
        self.client.close_session(self.session_id, error_code, "closed by state machine")

    # ========== invariant ==========

    @invariant()
    def session_ids_have_no_duplicates(self) -> None:
        """セッション ID の一覧に重複が無い

        終了した ID はどのタイミングで整理されてもよい (実装の解放方針に
        依存する) ため、残存の有無ではなく重複の不在を不変条件にする。
        """
        for session in (self.client, self.server):
            ids = session.get_session_ids()
            assert len(ids) == len(set(ids)), f"セッション ID が重複した: {ids}"

    @invariant()
    def session_closed_fires_at_most_once(self) -> None:
        """SessionClosed が side ごとに二重発火しない (0145 の回帰ピン)

        QPACK ブロック中に DATA / FIN がパイプラインされた経路でも、
        セッション終了通知は高々 1 回である。
        """
        assert self.client_closed_count <= 1, "クライアント側で SessionClosed が複数回発火した"
        assert self.server_closed_count <= 1, "サーバー側で SessionClosed が複数回発火した"

    @invariant()
    def no_error_events_for_valid_operations(self) -> None:
        """正当な API 操作系列では ERROR イベントが積まれない

        issue 0145 のクラッシュ経路は「正当なワイヤ列で発火する」もので
        あったため、正しい API 呼び出し系列でエラーイベントが観測されたら
        回帰として失敗させる。
        """
        events = _drain_events(self.server)
        error_events = [e for e in events if e.type == h3.EventType.ERROR]
        assert not error_events, f"ERROR イベントが発火した: {error_events[0].error_message}"

    def _observe_server_events(self) -> None:
        """サーバー側の受信イベントを観測する (データグラムの記録)"""
        for event in _drain_events(self.server):
            if event.type == h3.EventType.SESSION_CLOSED:
                self.server_closed_count += 1
            elif event.type == h3.EventType.DATAGRAM:
                self.received_datagrams.append(event.data)

    def _observe_client_events(self) -> None:
        """クライアント側の受信イベントを観測する"""
        for event in _drain_events(self.client):
            if event.type == h3.EventType.SESSION_CLOSED:
                self.client_closed_count += 1


TestH3EstablishedPair = H3EstablishedPairMachine.TestCase
TestH3EstablishedPair.settings = settings(
    # 確立済みペアへの多様な系列を踏ませるため、step 数と例数を確保する
    # (1 例あたり数 ms のため CI でも数秒に収まる)
    max_examples=50,
    stateful_step_count=30,
    deadline=None,
)
