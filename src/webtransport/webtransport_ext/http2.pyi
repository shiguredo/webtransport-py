"""HTTP/2 protocol (nghttp2)"""

import enum

class Config:
    """HTTP/2 設定"""

    def __init__(self) -> None: ...
    @property
    def initial_window_size(self) -> int:
        """初期ウィンドウサイズ"""

    @initial_window_size.setter
    def initial_window_size(self, arg: int, /) -> None: ...
    @property
    def max_concurrent_streams(self) -> int:
        """最大同時ストリーム数"""

    @max_concurrent_streams.setter
    def max_concurrent_streams(self, arg: int, /) -> None: ...
    @property
    def max_frame_size(self) -> int:
        """最大フレームサイズ"""

    @max_frame_size.setter
    def max_frame_size(self, arg: int, /) -> None: ...
    @property
    def max_header_list_size(self) -> int:
        """最大ヘッダーリストサイズ"""

    @max_header_list_size.setter
    def max_header_list_size(self, arg: int, /) -> None: ...
    @property
    def is_server(self) -> bool:
        """サーバーモード"""

    @is_server.setter
    def is_server(self, arg: bool, /) -> None: ...
    @property
    def no_rfc7540_priorities(self) -> bool:
        """SETTINGS_NO_RFC7540_PRIORITIES を送信するか"""

    @no_rfc7540_priorities.setter
    def no_rfc7540_priorities(self, arg: bool, /) -> None: ...

class EventType(enum.Enum):
    """HTTP/2 イベント種別"""

    HEADERS = 0

    DATA = 1

    STREAM_END = 2

    STREAM_RESET = 3

    GO_AWAY = 4

    WINDOW_UPDATE = 5

    SETTINGS = 6

    PING = 7

    PUSH_PROMISE = 8

    PRIORITY_UPDATE = 9

    INFORMATIONAL = 10

    TRAILERS = 11

class Event:
    """HTTP/2 イベント"""

    def __init__(self) -> None: ...
    @property
    def type(self) -> EventType:
        """イベント種別"""

    @property
    def stream_id(self) -> int:
        """ストリーム ID"""

    @property
    def headers(self) -> list[tuple[str, str]]:
        """ヘッダー"""

    @property
    def data(self) -> bytes:
        """データ"""

    @property
    def error_code(self) -> int:
        """エラーコード"""

    @property
    def last_stream_id(self) -> int:
        """GOAWAY の last_stream_id"""

    @property
    def promised_stream_id(self) -> int:
        """PUSH_PROMISE の promised stream ID"""

    @property
    def priority_field_value(self) -> str:
        """PRIORITY_UPDATE の priority field value"""

    @property
    def opaque_data(self) -> bytes:
        """PING の opaque data (RFC 9113 Section 6.7)"""

    @property
    def ack(self) -> bool:
        """PING ACK かどうか"""

    @property
    def window_size_increment(self) -> int:
        """WINDOW_UPDATE の増分値 (RFC 9113 Section 6.9)"""

class Connection:
    """HTTP/2 コネクション (Sans-IO)"""

    @staticmethod
    def create_client(config: Config) -> Connection:
        """クライアントとして接続を作成"""

    @staticmethod
    def create_server(config: Config) -> Connection:
        """サーバーとして接続を作成"""

    def receive(self, data: bytes) -> int:
        """受信したデータを処理"""

    def send(self) -> bytes | None:
        """送信すべきデータを取得"""

    def submit_request(self, headers: list[tuple[str, str]]) -> int:
        """リクエストを送信 (終端は send_data の eof=True で行う)"""

    def submit_response(self, stream_id: int, headers: list[tuple[str, str]]) -> None:
        """レスポンスを送信"""

    def send_data(self, stream_id: int, data: bytes, eof: bool = False) -> None:
        """ストリームにデータを送信"""

    def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセット"""

    def goaway(self, error_code: int = 0) -> None:
        """GOAWAY を送信"""

    def _test_force_close(self) -> None:
        """テスト専用: 低レベルを閉鎖状態にする (production からは呼ばない)"""

    def _test_stream_buffer_count(self, stream_id: int) -> int:
        """テスト専用: 送信バッファのエントリ数を返す"""

    def _test_stream_buffer_remaining(self, stream_id: int) -> int:
        """テスト専用: 送信バッファ先頭の残バイト数を返す"""

    def _test_stream_buffer_offset(self, stream_id: int) -> int:
        """テスト専用: 送信バッファ先頭の送信済みオフセットを返す"""

    def ping(self, opaque_data: bytes = b"") -> None:
        """PING を送信 (opaque_data は 8 バイト固定)"""

    def terminate_session(self, error_code: int = 0, last_stream_id: int = 0) -> bool:
        """GOAWAY を送信してセッションを即時終了"""

    def set_local_window_size(self, stream_id: int, window_size: int) -> bool:
        """ローカルウィンドウサイズを動的に変更"""

    def submit_trailer(self, stream_id: int, headers: list[tuple[str, str]]) -> bool:
        """トレーラを送信"""

    def submit_priority_update(self, stream_id: int, urgency: int, incremental: bool) -> bool:
        """PRIORITY_UPDATE フレームを送信"""

    def change_extpri_stream_priority(
        self, stream_id: int, urgency: int, incremental: bool
    ) -> bool:
        """ストリームの優先度を変更"""

    def submit_push_promise(self, stream_id: int, headers: list[tuple[str, str]]) -> int:
        """Server Push を宣言"""

    def next_event(self) -> Event | None:
        """次のイベントを取得"""

    def want_write(self) -> bool:
        """送信待ちデータがあるか"""

    def is_closed(self) -> bool:
        """接続が閉じられたか"""

    @property
    def remote_settings(self) -> dict[str, int] | None:
        """ピアの SETTINGS の値を取得"""

    @property
    def local_settings(self) -> dict[str, int] | None:
        """ローカルの SETTINGS の値を取得"""

    @property
    def outbound_queue_size(self) -> int | None:
        """送信キューのフレーム数を取得"""

    @property
    def remote_window_size(self) -> int | None:
        """コネクションのリモートウィンドウ残量を取得"""

    @property
    def local_window_size(self) -> int | None:
        """コネクションのローカルウィンドウ残量を取得"""

    @property
    def effective_recv_data_length(self) -> int | None:
        """WINDOW_UPDATE 未送信の受信 DATA バイト数を取得"""

    @property
    def request_allowed(self) -> bool | None:
        """新しいリクエストを送信できるかを取得"""

    def stream_remote_window_size(self, stream_id: int) -> int | None:
        """ストリームのリモートウィンドウ残量を取得"""

    def stream_local_window_size(self, stream_id: int) -> int | None:
        """ストリームのローカルウィンドウ残量を取得"""

    def stream_effective_recv_data_length(self, stream_id: int) -> int | None:
        """ストリームの WINDOW_UPDATE 未送信の受信 DATA バイト数を取得"""

    def stream_local_close(self, stream_id: int) -> bool | None:
        """ストリームのローカル側が half-closed かを取得"""

    def stream_remote_close(self, stream_id: int) -> bool | None:
        """ストリームのリモート側が half-closed かを取得"""

def get_version() -> str:
    """nghttp2 のバージョンを取得"""

def select_alpn(client_protocols: list[str]) -> str | None:
    """ALPN プロトコルを選択 (h2 / http/1.1 の優先順)"""
