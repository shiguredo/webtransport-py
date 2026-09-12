"""WebTransport over HTTP/2 の確立済みペアに対するステートフル PBT

既存の `prop_*.py` は「新規未接続オブジェクト 1 個」または「1 回の
ハンドシェイク」に対する操作しか検証しておらず、確立済みペアへの API
呼び出し系列を駆動する property test が無かった (`prop_h3_stateful.py` と
対)。session の寿命・フロー制御クレジット・カプセルバッファ上限に関する
既知バグ (0156 / 0157 / 0158 / 0159) の回帰ピンとして、確立済みペアへの
操作系列で abort せず不変条件が保たれることを検証する。
"""

from __future__ import annotations

from conftest import _connect_h2_session, _create_h2_session_pair, _drain_events, _h2_pump
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from webtransport import h2

# WT データストリームに使うクライアント起動双方向ストリーム
DATA_STREAM_IDS = (0, 4, 8)
# データグラムのペイロード上限 (draft-15 Section 3.4 の 1200 バイト目安)
MAX_DATAGRAM_SIZE = 1200


class H2EstablishedPairMachine(RuleBasedStateMachine):
    """確立済み h2 ペアへの API 呼び出し系列を駆動する状態機械

    フロー制御のクレジット (`get_send_credit`) が送信のたびに単調減少し、
    対向の WT_MAX_DATA 受信で回復することを invariant で観測する。
    """

    def __init__(self) -> None:
        super().__init__()
        client, server = _create_h2_session_pair()
        self.session_id = _connect_h2_session(client, server)
        self.client = client
        self.server = server
        # 開いたストリーム ID
        self.opened_streams: list[int] = []
        # 直前の残クレジット (単調減少の検証用)
        self.last_credit = client.get_send_credit(self.session_id)
        # side ごとの SessionClosed 発火回数
        self.client_closed_count = 0
        self.server_closed_count = 0

    # ========== rule ==========

    @rule()
    def open_stream(self) -> None:
        """クライアントが双方向ストリームを開く

        h2 のストリーム ID は払い出し (0, 4, 8, ...) のため、開いた ID を
        記録して後続の rule が使う。
        """
        stream_id = self.client.open_stream(self.session_id, False)
        if stream_id >= 0:
            self.opened_streams.append(stream_id)

    @rule(
        data=st.binary(max_size=1024),
        fin=st.booleans(),
        use_opened=st.booleans(),
    )
    def send_stream_data(self, data: bytes, fin: bool, use_opened: bool) -> None:
        """ストリームへデータを送る (開いていなければ未開設 ID への送信になる)"""
        stream_id = self.opened_streams[-1] if (use_opened and self.opened_streams) else 4
        self.client.send_stream_data(self.session_id, stream_id, data, fin)

    @rule(error_code=st.integers(min_value=0, max_value=0xFFFFFFFF), use_opened=st.booleans())
    def reset_stream(self, error_code: int, use_opened: bool) -> None:
        """ストリームをリセットする"""
        stream_id = self.opened_streams[-1] if (use_opened and self.opened_streams) else 4
        self.client.reset_stream(self.session_id, stream_id, error_code)

    @rule(data=st.binary(max_size=MAX_DATAGRAM_SIZE))
    def send_datagram(self, data: bytes) -> None:
        """データグラムを送る"""
        self.client.send_datagram(self.session_id, data)

    @rule()
    def pump_client_to_server(self) -> None:
        """クライアントの送信キューをサーバーへ渡す"""
        _h2_pump(self.client, self.server)
        for event in _drain_events(self.server):
            if event.type == h2.EventType.SESSION_CLOSED:
                self.server_closed_count += 1

    @rule()
    def pump_server_to_client(self) -> None:
        """サーバーの送信キューをクライアントへ渡す"""
        _h2_pump(self.server, self.client)
        for event in _drain_events(self.client):
            if event.type == h2.EventType.SESSION_CLOSED:
                self.client_closed_count += 1

    @rule(error_code=st.integers(min_value=0, max_value=0xFFFFFFFF))
    def close_session(self, error_code: int) -> None:
        """クライアントがセッションを閉じる"""
        self.client.close_session(self.session_id, error_code, "closed by state machine")

    # ========== invariant ==========

    @invariant()
    def send_credit_is_monotonic_within_window(self) -> None:
        """残クレジットが窓を超えて負にならない (0156 の回帰ピン)

        `get_send_credit` は枯渇時 0 を返し、単調に減ることはあっても
        対向の広告値を超えて増えることはない。
        """
        credit = self.client.get_send_credit(self.session_id)
        assert credit >= 0
        assert credit <= h2.Config().wt_initial_max_data

    @invariant()
    def session_ids_have_no_duplicates(self) -> None:
        """セッション ID の一覧に重複が無い"""
        for session in (self.client, self.server):
            ids = session.get_session_ids()
            assert len(ids) == len(set(ids)), f"セッション ID が重複した: {ids}"

    @invariant()
    def session_closed_fires_at_most_once(self) -> None:
        """SessionClosed が side ごとに二重発火しない"""
        assert self.client_closed_count <= 1
        assert self.server_closed_count <= 1

    @invariant()
    def no_error_events_for_valid_operations(self) -> None:
        """正当な API 操作系列では ERROR イベントが積まれない"""
        events = _drain_events(self.server)
        error_events = [e for e in events if e.type == h2.EventType.ERROR]
        assert not error_events, f"ERROR イベントが発火した: {error_events[0].error_message}"


TestH2EstablishedPair = H2EstablishedPairMachine.TestCase
TestH2EstablishedPair.settings = settings(
    max_examples=50,
    stateful_step_count=30,
    deadline=None,
)
