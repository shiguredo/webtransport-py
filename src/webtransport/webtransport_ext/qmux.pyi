"""QMux (dwnx) の Sans-IO API"""

import enum

class Config:
    """QMux 接続の設定"""

    def __init__(self) -> None: ...
    @property
    def initial_max_streams_bidi(self) -> int:
        """受け入れる同時双方向ストリーム数"""

    @initial_max_streams_bidi.setter
    def initial_max_streams_bidi(self, arg: int, /) -> None: ...
    @property
    def initial_max_streams_uni(self) -> int:
        """受け入れる同時単方向ストリーム数"""

    @initial_max_streams_uni.setter
    def initial_max_streams_uni(self, arg: int, /) -> None: ...
    @property
    def initial_max_data(self) -> int:
        """コネクション全体のフロー制御上限 (バイト)"""

    @initial_max_data.setter
    def initial_max_data(self, arg: int, /) -> None: ...
    @property
    def initial_max_stream_data_bidi_local(self) -> int:
        """双方向ストリーム (ローカル開始) のフロー制御上限 (バイト)"""

    @initial_max_stream_data_bidi_local.setter
    def initial_max_stream_data_bidi_local(self, arg: int, /) -> None: ...
    @property
    def initial_max_stream_data_bidi_remote(self) -> int:
        """双方向ストリーム (リモート開始) のフロー制御上限 (バイト)"""

    @initial_max_stream_data_bidi_remote.setter
    def initial_max_stream_data_bidi_remote(self, arg: int, /) -> None: ...
    @property
    def initial_max_stream_data_uni(self) -> int:
        """単方向ストリームのフロー制御上限 (バイト)"""

    @initial_max_stream_data_uni.setter
    def initial_max_stream_data_uni(self, arg: int, /) -> None: ...
    @property
    def max_idle_timeout_ns(self) -> int:
        """アイドルタイムアウト (ナノ秒)"""

    @max_idle_timeout_ns.setter
    def max_idle_timeout_ns(self, arg: int, /) -> None: ...
    @property
    def max_record_size(self) -> int:
        """1 レコードの最大長 (バイト)"""

    @max_record_size.setter
    def max_record_size(self, arg: int, /) -> None: ...

class EventType(enum.Enum):
    """QMux イベント種別"""

    TRANSPORT_PARAMS_RECEIVED = 0

    STREAM_DATA = 1

    STREAM_CLOSED = 2

    STREAM_RESET = 3

    STOP_SENDING = 4

class Event:
    """QMux イベント"""

    def __init__(self) -> None: ...
    @property
    def type(self) -> EventType:
        """イベント種別"""

    @property
    def stream_id(self) -> int:
        """ストリーム ID"""

    @property
    def offset(self) -> int:
        """ストリーム上のオフセット"""

    @property
    def data(self) -> bytes:
        """ストリームデータ"""

    @property
    def fin(self) -> bool:
        """FIN フラグ"""

    @property
    def error_code(self) -> int:
        """エラーコード"""

class Connection:
    """QMux コネクション (Sans-IO)"""

    @staticmethod
    def create_client(config: Config) -> Connection:
        """クライアントとして接続を作成"""

    @staticmethod
    def create_server(config: Config) -> Connection:
        """サーバーとして接続を作成"""

    def receive(self, data: bytes) -> int:
        """受信したバイト列を処理する (負値はライブラリエラー)"""

    @property
    def pending_record(self) -> bytes | None:
        """送信すべき 1 レコード (無い場合は None)"""

    @property
    def timeout(self) -> int | None:
        """次のタイマー期限 (ナノ秒)"""

    def handle_timeout(self) -> None:
        """タイマーを処理する"""

    def open_stream(self, bidirectional: bool = True) -> int:
        """ストリームを開く (失敗時は -1)"""

    def send_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """ストリームデータの送信を予約する"""

    def close(self, error_code: int = 0, reason: str = "") -> None:
        """接続を閉じる (CONNECTION_CLOSE のレコードを取り出せるようにする)"""

    def next_event(self) -> Event | None:
        """次のイベントを取得する"""

    @property
    def is_server(self) -> bool:
        """サーバー接続かどうか"""

    @property
    def streams_bidi_left(self) -> int:
        """開設可能な残り双方向ストリーム数"""

    @property
    def streams_uni_left(self) -> int:
        """開設可能な残り単方向ストリーム数"""

    @staticmethod
    def strerror(liberr: int) -> str:
        """ライブラリエラーコードを文字列にする"""

def get_version() -> str:
    """dwnx のバージョン"""
