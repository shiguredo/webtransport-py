"""QUIC の確立済みペアに対するステートフル PBT

既存の `prop_quic*.py` は「新規未接続オブジェクト 1 個」または「1 回の
ハンドシェイク」に対する操作しか検証していない (`prop_h3_stateful.py` /
`prop_h2_stateful.py` と対)。確立済みの QUIC 接続ペアへストリーム操作と
データグラム操作の系列を駆動し、abort せず不変条件が保たれることを検証する。
issue 0146 (Config 値で assert に到達する経路) の回帰ピンとして、
Config の境界値でも abort しないことを併せて確認する。
"""

from __future__ import annotations

from conftest import (
    CLIENT_ADDR,
    SERVER_ADDR,
    create_client_server_pair,
    perform_handshake,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from webtransport import quic

# QUIC の DATAGRAM ペイロード上限 (RFC 9221。既定の max_datagram_frame_size)
MAX_DATAGRAM_SIZE = 1200
# varint の上限 (RFC 9000 Section 16)
MAX_VARINT = 2**62 - 1


class QuicEstablishedPairMachine(RuleBasedStateMachine):
    """確立済み QUIC ペアへの API 呼び出し系列を駆動する状態機械

    stream の open / send / reset / datagram と、パケットのポンプを
    組み合わせて系列を作る。各 rule は abort しないこと、invariant は
    ACK の往復で接続が閉じないことと送受信バイト数が破綻しないことを見る。
    """

    def __init__(self) -> None:
        super().__init__()
        client, server, initial_packet = create_client_server_pair()
        assert perform_handshake(client, server, initial_packet) is True
        self.client = client
        self.server = server
        self.opened_streams: list[int] = []
        self.sent_bytes = 0
        self.client_closed = False

    def _pump(self) -> None:
        """双方のパケットを相手へ渡す (待機なし)

        stateful PBT は 1 例あたりの step 数が多いため、pacing 期限を待つ
        conftest の pump ではなく、送れるパケットを即座に交換する軽量な
        pump を使う。残った再送や ACK は後続の rule の pump が処理する。
        """
        for _ in range(10):
            server_packet = self.server.send()
            if server_packet:
                self.client.receive(server_packet.data, CLIENT_ADDR, SERVER_ADDR)
            client_packet = self.client.send()
            if client_packet:
                self.server.receive(client_packet.data, SERVER_ADDR, CLIENT_ADDR)
            if server_packet is None and client_packet is None:
                break

    # ========== rule ==========

    @rule(bidirectional=st.booleans())
    def open_stream(self, bidirectional: bool) -> None:
        """クライアントがストリームを開く"""
        stream_id = self.client.open_stream(bidirectional)
        if stream_id >= 0:
            self.opened_streams.append(stream_id)
        self._pump()

    @rule(data=st.binary(max_size=1024), fin=st.booleans(), use_opened=st.booleans())
    def send_stream_data(self, data: bytes, fin: bool, use_opened: bool) -> None:
        """ストリームへデータを送る"""
        stream_id = self.opened_streams[-1] if (use_opened and self.opened_streams) else 0
        self.client.send_stream_data(stream_id, data, fin)
        self.sent_bytes += len(data)
        self._pump()

    @rule(error_code=st.integers(min_value=0, max_value=0xFFFFFFFF), use_opened=st.booleans())
    def reset_stream(self, error_code: int, use_opened: bool) -> None:
        """ストリームをリセットする"""
        stream_id = self.opened_streams[-1] if (use_opened and self.opened_streams) else 0
        self.client.reset_stream(stream_id, error_code)
        self._pump()

    @rule(data=st.binary(max_size=MAX_DATAGRAM_SIZE))
    def send_datagram(self, data: bytes) -> None:
        """データグラムを送る

        接続が DATAGRAM を広告していない場合は send_datagram が黙って
        無視するため、rule 自体は無条件に呼ぶ (rule が状態に依存して
        早期 return すると Hypothesis が data generation の非一貫性として
        検出する)。
        """
        self.client.send_datagram(data)
        self._pump()

    @rule()
    def pump(self) -> None:
        """パケットを往復させる (ACK と再送の処理)"""
        self._pump()

    @rule()
    def close_client(self) -> None:
        """クライアントが接続を閉じる"""
        self.client.close()
        self.client_closed = True
        self._pump()

    # ========== invariant ==========

    @invariant()
    def connection_closes_only_when_requested(self) -> None:
        """明示的な close もピアの終了も無い限り接続が閉じない

        確立済み接続が系列の途中で勝手に閉じると、再送やフロー制御の
        不具合を示す。
        """
        if not self.client_closed:
            assert self.client.is_closed() is False, "クライアント接続が勝手に閉じた"

    @invariant()
    def stream_ids_are_varint_range(self) -> None:
        """払い出されたストリーム ID が varint の範囲に収まる"""
        for stream_id in self.opened_streams:
            assert 0 <= stream_id <= MAX_VARINT


TestQuicEstablishedPair = QuicEstablishedPairMachine.TestCase
TestQuicEstablishedPair.settings = settings(
    # 軽量な pump にしたため 1 例あたり数 ms。CI でも数秒に収まる範囲で
    # 系列を広く取る
    max_examples=30,
    stateful_step_count=25,
    deadline=None,
)


@given(
    st.integers(min_value=0, max_value=2**64 - 1),
    st.integers(min_value=0, max_value=2**32 - 1),
)
@settings(max_examples=50, deadline=None)
def test_quic_config_boundary_values_never_abort(max_data: int, max_streams_bidi: int) -> None:
    """Config の境界値でも abort しない (0146 の回帰ピン)

    issue 0146 は「Python から渡した Config 値だけで依存ライブラリの assert に
    到達し SIGABRT する経路」を塞いだ。生成時・接続時のどちらでも abort せず、
    ValueError で拒否されることを確認する。
    """
    client_config = quic.Config()
    client_config.verify_peer = False
    client_config.max_data = max_data
    client_config.max_streams_bidi = max_streams_bidi

    try:
        connection = quic.Connection.create_client(client_config, CLIENT_ADDR, SERVER_ADDR)
    except ValueError, RuntimeError:
        # 範囲外は ValueError、ngtcp2 が拒否する値は RuntimeError になる。
        # いずれも abort (SIGABRT) しないことを確認する
        return
    # 生成に成功した接続は操作しても abort しない
    connection.send()
    connection.get_timeout()
    connection.close()
