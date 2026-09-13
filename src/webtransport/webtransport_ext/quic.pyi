"""QUIC protocol (ngtcp2)"""

import enum
from collections.abc import Sequence

class Config:
    """QUIC コネクション設定"""

    def __init__(self) -> None: ...
    @property
    def max_streams_bidi(self) -> int:
        """最大双方向ストリーム数"""

    @max_streams_bidi.setter
    def max_streams_bidi(self, arg: int, /) -> None: ...
    @property
    def max_streams_uni(self) -> int:
        """最大単方向ストリーム数"""

    @max_streams_uni.setter
    def max_streams_uni(self, arg: int, /) -> None: ...
    @property
    def max_data(self) -> int:
        """最大データサイズ"""

    @max_data.setter
    def max_data(self, arg: int, /) -> None: ...
    @property
    def max_stream_data_bidi_local(self) -> int:
        """ローカル双方向ストリームの最大データサイズ"""

    @max_stream_data_bidi_local.setter
    def max_stream_data_bidi_local(self, arg: int, /) -> None: ...
    @property
    def max_stream_data_bidi_remote(self) -> int:
        """リモート双方向ストリームの最大データサイズ"""

    @max_stream_data_bidi_remote.setter
    def max_stream_data_bidi_remote(self, arg: int, /) -> None: ...
    @property
    def max_stream_data_uni(self) -> int:
        """単方向ストリームの最大データサイズ"""

    @max_stream_data_uni.setter
    def max_stream_data_uni(self, arg: int, /) -> None: ...
    @property
    def idle_timeout_ns(self) -> int:
        """アイドルタイムアウト (ナノ秒)"""

    @idle_timeout_ns.setter
    def idle_timeout_ns(self, arg: int, /) -> None: ...
    @property
    def alpn_protocols(self) -> list[str]:
        """ALPN プロトコル"""

    @alpn_protocols.setter
    def alpn_protocols(self, arg: Sequence[str], /) -> None: ...
    @property
    def server_name(self) -> str:
        """サーバー名 (SNI)"""

    @server_name.setter
    def server_name(self, arg: str, /) -> None: ...
    @property
    def cert_file(self) -> str:
        """証明書ファイルパス"""

    @cert_file.setter
    def cert_file(self, arg: str, /) -> None: ...
    @property
    def key_file(self) -> str:
        """秘密鍵ファイルパス"""

    @key_file.setter
    def key_file(self, arg: str, /) -> None: ...
    @property
    def verify_peer(self) -> bool:
        """クライアントのピア証明書検証を行うか"""

    @verify_peer.setter
    def verify_peer(self, arg: bool, /) -> None: ...
    @property
    def ca_file(self) -> str:
        """CA 証明書ファイルパス"""

    @ca_file.setter
    def ca_file(self, arg: str, /) -> None: ...
    @property
    def verify_callback(self) -> object:
        """
        ピア証明書検証コールバック (list[bytes] -> bool) または None。コールバック内で同一 Connection のメソッドを呼ばないこと
        """

    @verify_callback.setter
    def verify_callback(self, arg: object, /) -> None: ...
    @property
    def enable_datagram(self) -> bool:
        """Datagram を有効にするか"""

    @enable_datagram.setter
    def enable_datagram(self, arg: bool, /) -> None: ...
    @property
    def max_datagram_frame_size(self) -> int:
        """最大 Datagram フレームサイズ"""

    @max_datagram_frame_size.setter
    def max_datagram_frame_size(self, arg: int, /) -> None: ...
    @property
    def enable_reset_stream_at(self) -> bool:
        """RESET_STREAM_AT の受信対応を広告するか"""

    @enable_reset_stream_at.setter
    def enable_reset_stream_at(self, arg: bool, /) -> None: ...
    @property
    def enable_early_data(self) -> bool:
        """0-RTT early data を有効にするか"""

    @enable_early_data.setter
    def enable_early_data(self, arg: bool, /) -> None: ...
    @property
    def session_ticket(self) -> bytes:
        """セッションチケット (DER bytes)"""

    @session_ticket.setter
    def session_ticket(self, arg: bytes, /) -> None: ...
    @property
    def early_transport_params(self) -> bytes:
        """0-RTT トランスポートパラメータ (bytes)"""

    @early_transport_params.setter
    def early_transport_params(self, arg: bytes, /) -> None: ...

class EventType(enum.Enum):
    """QUIC イベント種別"""

    HANDSHAKE_COMPLETED = 0

    CONNECTION_CLOSED = 1

    STREAM_DATA = 2

    STREAM_OPENED = 3

    STREAM_CLOSED = 4

    STREAM_RESET = 5

    DATAGRAM = 6

    SESSION_TICKET = 7

    EARLY_DATA_REJECTED = 8

    PATH_VALIDATED = 9

    PATH_VALIDATION_FAILED = 10

    STOP_SENDING = 11

class ReceiveResult(enum.Enum):
    """QUIC パケットの受信結果"""

    ACCEPTED = 0

    DISCARDED = 1

    CLOSED = 2

class Event:
    """QUIC イベント"""

    def __init__(self) -> None: ...
    @property
    def type(self) -> EventType:
        """イベント種別"""

    @property
    def stream_id(self) -> int:
        """ストリーム ID"""

    @property
    def data(self) -> bytes:
        """データ"""

    @property
    def fin(self) -> bool:
        """FIN フラグ"""

    @property
    def error_code(self) -> int:
        """エラーコード"""

    @property
    def reason(self) -> str:
        """理由"""

    @property
    def offset(self) -> int:
        """STREAM_DATA イベントのストリーム上のオフセット (他イベントでは 0)"""

class Packet:
    """QUIC UDP パケット (パス情報付き)"""

    def __init__(self) -> None: ...
    @property
    def data(self) -> bytes:
        """パケットデータ"""

    @property
    def local_host(self) -> str:
        """ローカルホスト"""

    @property
    def local_port(self) -> int:
        """ローカルポート"""

    @property
    def remote_host(self) -> str:
        """リモートホスト"""

    @property
    def remote_port(self) -> int:
        """リモートポート"""

class Connection:
    """QUIC コネクション (Sans-IO)"""

    @staticmethod
    def create_client(
        config: Config, local_addr: tuple[str, int], remote_addr: tuple[str, int]
    ) -> Connection:
        """クライアントとして接続を作成"""

    @staticmethod
    def create_server(config: Config) -> Connection:
        """サーバーとして接続を作成"""

    @staticmethod
    def accept(
        config: Config,
        initial_packet: bytes,
        local_addr: tuple[str, int],
        remote_addr: tuple[str, int],
    ) -> Connection:
        """初期パケットからサーバー接続を作成"""

    def receive(
        self, data: bytes, local_addr: tuple[str, int], remote_addr: tuple[str, int]
    ) -> ReceiveResult:
        """受信したデータを処理"""

    def send(self) -> Packet | None:
        """送信すべきデータを取得"""

    def initiate_migration(self, local_addr: tuple[str, int], remote_addr: tuple[str, int]) -> bool:
        """コネクションマイグレーションを開始する"""

    def export_session_ticket(self) -> bytes:
        """セッションチケット (DER) を取得"""

    def export_0rtt_transport_params(self) -> bytes:
        """0-RTT トランスポートパラメータを取得"""

    def is_early_data_accepted(self) -> bool:
        """0-RTT early data が受理されたか"""

    def was_early_data_attempted(self) -> bool:
        """0-RTT early data を試みたか"""

    def get_timeout(self) -> int | None:
        """次のタイムアウトまでの時間を取得 (ナノ秒)"""

    def handle_timeout(self) -> None:
        """タイムアウトを処理"""

    def open_stream(self, bidirectional: bool = True) -> int:
        """ストリームを開く"""

    @property
    def streams_bidi_left(self) -> int | None:
        """開設可能な残り双方向ストリーム数"""

    @property
    def streams_uni_left(self) -> int | None:
        """開設可能な残り単方向ストリーム数"""

    def keep_alive_timeout(self, timeout_ns: int) -> None:
        """keep-alive タイムアウトを設定 (ナノ秒。UINT64_MAX で無効化)"""

    def initiate_key_update(self) -> bool:
        """鍵更新を開始 (成功で True)"""

    def extend_max_offset(self, datalen: int) -> None:
        """コネクション全体のフロー制御を拡張 (バイト)"""

    def extend_max_stream_offset(self, stream_id: int, datalen: int) -> bool:
        """ストリームのフロー制御を拡張 (バイト。成功で True)"""

    def extend_max_streams_bidi(self, n: int) -> None:
        """双方向ストリーム上限を拡張"""

    def extend_max_streams_uni(self, n: int) -> None:
        """単方向ストリーム上限を拡張"""

    def send_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """ストリームにデータを送信"""

    def close_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームを閉じる (RESET_STREAM + STOP_SENDING)"""

    def stop_sending(self, stream_id: int, error_code: int = 0) -> None:
        """STOP_SENDING を送出する"""

    def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """RESET_STREAM を送出する"""

    def send_datagram(self, data: bytes) -> None:
        """Datagram を送信"""

    def close(self, error_code: int = 0, reason: str = "") -> None:
        """接続を閉じる"""

    def next_event(self) -> Event | None:
        """次のイベントを取得"""

    def is_established(self) -> bool:
        """接続が確立されているか"""

    def is_closed(self) -> bool:
        """接続が閉じられたか"""

    def is_handshake_completed(self) -> bool:
        """ハンドシェイクが完了したか"""

    def get_connection_id(self) -> bytes:
        """接続 ID を取得"""

    @property
    def latest_rtt(self) -> int | None:
        """最新の RTT (ナノ秒)"""

    @property
    def min_rtt(self) -> int | None:
        """最小の RTT (ナノ秒)"""

    @property
    def smoothed_rtt(self) -> int | None:
        """平滑化された RTT (ナノ秒)"""

    @property
    def rttvar(self) -> int | None:
        """RTT の平均偏差 (ナノ秒)"""

    @property
    def cwnd(self) -> int | None:
        """輻輳ウィンドウ (バイト)"""

    @property
    def ssthresh(self) -> int | None:
        """スロー スタート閾値 (バイト)"""

    @property
    def bytes_in_flight(self) -> int | None:
        """送信中で未 ACK のバイト数"""

    @property
    def pkt_sent(self) -> int | None:
        """送信したパケット数"""

    @property
    def bytes_sent(self) -> int | None:
        """送信したバイト数"""

    @property
    def pkt_recv(self) -> int | None:
        """受信したパケット数 (破棄パケット除外)"""

    @property
    def bytes_recv(self) -> int | None:
        """受信したバイト数 (破棄パケット除外)"""

    @property
    def pkt_lost(self) -> int | None:
        """損失したパケット数 (PMTUD パケット除外)"""

    @property
    def bytes_lost(self) -> int | None:
        """損失したバイト数 (PMTUD パケット除外)"""

    @property
    def ping_recv(self) -> int | None:
        """受信した PING フレーム数"""

    @property
    def pkt_discarded(self) -> int | None:
        """破棄したパケット数"""

    @property
    def pto(self) -> int | None:
        """PTO (プローブタイムアウト) (ナノ秒)"""

    @property
    def cwnd_left(self) -> int | None:
        """輻輳ウィンドウ残量 (バイト)"""

    @property
    def max_data_left(self) -> int | None:
        """コネクション全体のフロー制御残量 (バイト)"""

    def max_stream_data_left(self, stream_id: int) -> int | None:
        """ストリームごとのフロー制御残量 (バイト)"""

    def stream_loss_count(self, stream_id: int) -> int | None:
        """STREAM フレームを含む損失パケット数 (スプリアス損失を含む)"""

    @property
    def send_quantum(self) -> int | None:
        """送信クォンタム (バイト)"""

    @property
    def path_max_tx_udp_payload_size(self) -> int | None:
        """現在パスの最大 UDP ペイロードサイズ (バイト)"""

    @property
    def error_code(self) -> int | None:
        """コネクションエラーのコード (エラーが無い場合は None)"""

    @property
    def reason(self) -> str | None:
        """コネクションエラーの理由 (エラーが無い場合は None)"""

    @property
    def tls_error(self) -> int:
        """TLS 処理時に ngtcp2 が記録した内部エラーコード (無ければ 0)"""

    @property
    def tls_alert(self) -> int:
        """TLS アラート (エラーが無い場合は 0)"""

    @property
    def remote_max_idle_timeout(self) -> int | None:
        """ピアのアイドルタイムアウト (ナノ秒)"""

    @property
    def remote_max_udp_payload_size(self) -> int | None:
        """ピアの最大 UDP ペイロードサイズ (バイト)"""

    @property
    def remote_initial_max_data(self) -> int | None:
        """ピアのコネクション全体のフロー制御上限"""

    @property
    def remote_initial_max_stream_data_bidi_local(self) -> int | None:
        """ピアの双方向ストリーム (ローカル開始) のフロー制御上限"""

    @property
    def remote_initial_max_stream_data_bidi_remote(self) -> int | None:
        """ピアの双方向ストリーム (リモート開始) のフロー制御上限"""

    @property
    def remote_initial_max_stream_data_uni(self) -> int | None:
        """ピアの単方向ストリームのフロー制御上限"""

    @property
    def remote_initial_max_streams_bidi(self) -> int | None:
        """ピアの双方向ストリーム並列数上限"""

    @property
    def remote_initial_max_streams_uni(self) -> int | None:
        """ピアの単方向ストリーム並列数上限"""

    @property
    def remote_max_datagram_frame_size(self) -> int | None:
        """ピアの Datagram フレームサイズ上限"""

    @property
    def remote_reset_stream_at(self) -> bool | None:
        """ピアが reset_stream_at transport parameter を送信したか"""

    @property
    def local_max_idle_timeout(self) -> int:
        """ローカルのアイドルタイムアウト (ナノ秒)"""

    @property
    def local_max_udp_payload_size(self) -> int:
        """ローカルの最大 UDP ペイロードサイズ (バイト)"""

    @property
    def local_initial_max_data(self) -> int:
        """ローカルのコネクション全体のフロー制御上限"""

    @property
    def local_initial_max_stream_data_bidi_local(self) -> int:
        """ローカルの双方向ストリーム (ローカル開始) のフロー制御上限"""

    @property
    def local_initial_max_stream_data_bidi_remote(self) -> int:
        """ローカルの双方向ストリーム (リモート開始) のフロー制御上限"""

    @property
    def local_initial_max_stream_data_uni(self) -> int:
        """ローカルの単方向ストリームのフロー制御上限"""

    @property
    def local_initial_max_streams_bidi(self) -> int:
        """ローカルの双方向ストリーム並列数上限"""

    @property
    def local_initial_max_streams_uni(self) -> int:
        """ローカルの単方向ストリーム並列数上限"""

    @property
    def local_max_datagram_frame_size(self) -> int:
        """ローカルの Datagram フレームサイズ上限"""

    @property
    def negotiated_version(self) -> int:
        """ネゴシエーションされた QUIC バージョン (未確定なら 0)"""

    @property
    def client_chosen_version(self) -> int:
        """クライアントが選択した QUIC バージョン"""

    @property
    def in_closing_period(self) -> bool:
        """CLOSING 状態か"""

    @property
    def in_draining_period(self) -> bool:
        """DRAINING 状態か"""

    @property
    def scid(self) -> list[bytes]:
        """送信元接続 ID (SCID) の一覧"""

    @property
    def active_dcid(self) -> list[bytes]:
        """アクティブな宛先接続 ID (DCID) の一覧 (ハンドシェイク完了前は空)"""

def get_version() -> str:
    """ngtcp2 のバージョンを取得"""
