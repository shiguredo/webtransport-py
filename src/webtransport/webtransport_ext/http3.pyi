"""HTTP/3 protocol (nghttp3)"""

import enum

class Config:
    """HTTP/3 設定"""

    def __init__(self) -> None: ...
    @property
    def max_field_section_size(self) -> int:
        """最大フィールドセクションサイズ"""

    @max_field_section_size.setter
    def max_field_section_size(self, arg: int, /) -> None: ...
    @property
    def qpack_max_dtable_capacity(self) -> int:
        """QPACK 動的テーブル最大容量"""

    @qpack_max_dtable_capacity.setter
    def qpack_max_dtable_capacity(self, arg: int, /) -> None: ...
    @property
    def qpack_blocked_streams(self) -> int:
        """QPACK ブロックされたストリーム数"""

    @qpack_blocked_streams.setter
    def qpack_blocked_streams(self, arg: int, /) -> None: ...
    @property
    def enable_webtransport(self) -> bool:
        """WebTransport 有効化"""

    @enable_webtransport.setter
    def enable_webtransport(self, arg: bool, /) -> None: ...
    @property
    def enable_h3_datagram(self) -> bool:
        """HTTP/3 Datagram 有効化"""

    @enable_h3_datagram.setter
    def enable_h3_datagram(self, arg: bool, /) -> None: ...
    @property
    def is_server(self) -> bool:
        """サーバーモード"""

    @is_server.setter
    def is_server(self, arg: bool, /) -> None: ...

class EventType(enum.Enum):
    """HTTP/3 イベント種別"""

    HEADERS = 0

    DATA = 1

    STREAM_END = 2

    GO_AWAY = 3

    RESET_STREAM = 4

    STOP_SENDING = 5

    INFORMATIONAL = 6

    TRAILERS = 7

    ERROR = 8

class Event:
    """HTTP/3 イベント"""

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
    def error_message(self) -> str:
        """Error イベントのエラーメッセージ (他イベントでは空)"""

    @property
    def push_id(self) -> int:
        """Push ID"""

class Connection:
    """HTTP/3 コネクション (Sans-IO)"""

    @staticmethod
    def create_client(config: Config) -> Connection:
        """クライアントとして接続を作成"""

    @staticmethod
    def create_server(config: Config) -> Connection:
        """サーバーとして接続を作成"""

    def receive_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> int:
        """QUIC ストリームからデータを受信"""

    def get_streams_to_send(self) -> list[tuple[int, bytes, bool]]:
        """送信すべきストリームデータを取得"""

    def bind_control_stream(self, stream_id: int) -> None:
        """コントロールストリームを設定"""

    def bind_qpack_encoder_stream(self, stream_id: int) -> None:
        """QPACK エンコーダーストリームを設定"""

    def bind_qpack_decoder_stream(self, stream_id: int) -> None:
        """QPACK デコーダーストリームを設定"""

    def submit_request(self, stream_id: int, headers: list[tuple[str, str]]) -> bool:
        """リクエストを送信"""

    def submit_response(self, stream_id: int, headers: list[tuple[str, str]]) -> bool:
        """レスポンスを送信"""

    def send_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """ストリームにデータを送信"""

    def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """ストリームをリセット"""

    def _test_force_close(self) -> None:
        """テスト専用: 低レベルを閉鎖状態にする (production からは呼ばない)"""

    def close_stream(self, stream_id: int, error_code: int = 0x0100) -> None:
        """QUIC ストリーム終了を nghttp3 に通知する (既定は H3_NO_ERROR)"""

    def goaway(self) -> None:
        """GOAWAY を送信 (GOAWAY ID は nghttp3 が算出する)"""

    def submit_trailers(self, stream_id: int, headers: list[tuple[str, str]]) -> bool:
        """トレーラを送信"""

    def submit_info(self, stream_id: int, headers: list[tuple[str, str]]) -> bool:
        """1xx レスポンスを送信 (サーバーのみ)"""

    def submit_shutdown_notice(self) -> bool:
        """graceful shutdown の開始通知を送信 (サーバーのみ)"""

    def shutdown_stream_write(self, stream_id: int) -> None:
        """ストリームの書き込み側をシャットダウン"""

    def next_event(self) -> Event | None:
        """次のイベントを取得"""

    def get_required_streams(self) -> list[tuple[str, bool]]:
        """必要な QUIC ストリームのリストを取得"""

    def is_closed(self) -> bool:
        """接続が閉じられたか"""

    def stream_writable(self, stream_id: int) -> int | None:
        """ストリームが書き込み可能か確認"""

    def stream_flushed(self, stream_id: int) -> int | None:
        """ストリームの全送信データが QUIC スタックに受け渡し済みか確認"""

    def _has_stream_buffer(self, stream_id: int) -> bool | None:
        """テスト専用: ストリームの送信バッファエントリの有無を確認"""

    def frame_payload_left(self, stream_id: int) -> int | None:
        """受信中フレームのペイロード残量を取得"""

    @property
    def drained(self) -> bool | None:
        """ドレイン状態か確認 (サーバーのみ)"""

    def stream_priority(self, stream_id: int) -> tuple[int, bool] | None:
        """ストリームの優先度を取得 (サーバーのみ)"""

    def set_max_client_streams_bidi(self, max_streams: int) -> None:
        """クライアントからの双方向ストリームの最大数を設定 (サーバーのみ)"""

    def client_stream_priority(self, stream_id: int, urgency: int, incremental: bool) -> bool:
        """クライアント起動双方向ストリームの優先度を設定 (クライアントのみ)"""

    def server_stream_priority(self, stream_id: int, urgency: int, incremental: bool) -> bool:
        """クライアント起動双方向ストリームの優先度を設定 (サーバーのみ)"""

    def block_stream(self, stream_id: int) -> None:
        """ストリームの QUIC フロー制御ブロックを通知"""

    def unblock_stream(self, stream_id: int) -> bool:
        """ストリームの QUIC フロー制御ブロック解除を通知"""

    def max_concurrent_streams(self, n: int) -> None:
        """同時ストリーム数のヒントを設定"""

def get_version() -> str:
    """nghttp3 のバージョンを取得"""

def parse_priority(value: str) -> tuple[int, bool] | None:
    """RFC 9218 の Priority ヘッダー値をパース"""
