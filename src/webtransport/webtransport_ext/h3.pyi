"""WebTransport over HTTP/3"""

import enum
from collections.abc import Sequence

class Config:
    """WebTransport over HTTP/3 設定"""

    def __init__(self) -> None: ...
    @property
    def max_field_section_size(self) -> int: ...
    @max_field_section_size.setter
    def max_field_section_size(self, arg: int, /) -> None: ...
    @property
    def qpack_max_dtable_capacity(self) -> int: ...
    @qpack_max_dtable_capacity.setter
    def qpack_max_dtable_capacity(self, arg: int, /) -> None: ...
    @property
    def qpack_blocked_streams(self) -> int: ...
    @qpack_blocked_streams.setter
    def qpack_blocked_streams(self, arg: int, /) -> None: ...
    @property
    def wt_pre_accept_buffer_limit(self) -> int:
        """
        受理前 WebTransport データストリーム 1 本あたりの累計受信バイト上限 (0 で即時拒否。超過時は WT_BUFFERED_STREAM_REJECTED で拒否)
        """

    @wt_pre_accept_buffer_limit.setter
    def wt_pre_accept_buffer_limit(self, arg: int, /) -> None: ...
    @property
    def is_server(self) -> bool: ...
    @is_server.setter
    def is_server(self, arg: bool, /) -> None: ...
    @property
    def allowed_origins(self) -> list[str]:
        """許可オリジンリスト (空なら全オリジンを受理)"""

    @allowed_origins.setter
    def allowed_origins(self, arg: Sequence[str], /) -> None: ...

class EventType(enum.Enum):
    """WebTransport イベント種別"""

    SESSION_READY = 0

    SESSION_CLOSED = 1

    STREAM_DATA = 2

    STREAM_CLOSED = 3

    RESET_STREAM = 4

    STOP_SENDING = 5

    DATAGRAM = 6

    ERROR = 7

    SESSION_REJECTED = 8

class Event:
    """WebTransport イベント"""

    def __init__(self) -> None: ...
    @property
    def type(self) -> EventType: ...
    @property
    def session_id(self) -> int: ...
    @property
    def stream_id(self) -> int: ...
    @property
    def data(self) -> bytes:
        """イベントデータ"""

    @property
    def error_code(self) -> int: ...
    @property
    def error_message(self) -> str: ...
    @property
    def status_code(self) -> int:
        """SessionRejected 発火時の HTTP status code。他イベントでは 0 (パース失敗・範囲外は 0 に丸められる)"""

    @property
    def headers(self) -> list[tuple[str, str]]:
        """SESSION_READY 発火時の受信 CONNECT ヘッダー。他イベントでは空"""

class StreamInfo:
    """WebTransport ストリーム情報"""

    def __init__(self) -> None: ...
    @property
    def stream_id(self) -> int: ...
    @property
    def session_id(self) -> int: ...
    @property
    def is_unidirectional(self) -> bool: ...
    @property
    def is_incoming(self) -> bool: ...
    @property
    def is_write_registered(self) -> bool: ...

class Session:
    """WebTransport over HTTP/3 セッション"""

    @staticmethod
    def create_client(config: Config) -> Session:
        """クライアントセッションを作成"""

    @staticmethod
    def create_server(config: Config) -> Session:
        """サーバーセッションを作成"""

    def receive_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> int:
        """QUIC ストリームからデータを受信 (data が 1 MiB 超の場合は ValueError)"""

    def receive_datagram(self, data: bytes) -> None:
        """QUIC データグラムを受信 (data が 1 MiB 超の場合は ValueError)"""

    def get_streams_to_send(self) -> list[tuple[int, bytes, bool]]:
        """送信すべきストリームデータを取得"""

    def get_datagrams_to_send(self) -> list[bytes]:
        """送信すべきデータグラムを取得"""

    def bind_control_stream(self, stream_id: int) -> None:
        """コントロールストリーム ID を設定"""

    def bind_qpack_encoder_stream(self, stream_id: int) -> None:
        """QPACK エンコーダーストリーム ID を設定"""

    def bind_qpack_decoder_stream(self, stream_id: int) -> None:
        """QPACK デコーダーストリーム ID を設定"""

    def connect(self, stream_id: int, url: str, origin: str = "") -> bool:
        """WebTransport セッションを開始 (クライアント用)"""

    def accept_session(self, stream_id: int) -> bool:
        """WebTransport セッションを受理 (サーバー用)"""

    def reject_session(self, stream_id: int, status_code: int) -> None:
        """
        WebTransport セッションを拒否 (サーバー用。405 の場合は Allow: CONNECT を応答に含める。非 WebTransport リクエストへの 405 応答にも使う)
        """

    def open_stream(self, session_id: int, stream_id: int, is_unidirectional: bool) -> bool:
        """WebTransport ストリームを開く"""

    def send_stream_data(self, stream_id: int, data: bytes, fin: bool = False) -> None:
        """WebTransport ストリームにデータを送信 (data が 1 MiB 超の場合は ValueError)"""

    def send_datagram(self, session_id: int, data: bytes) -> None:
        """WebTransport データグラムを送信 (data が 1 MiB 超の場合は ValueError)"""

    def close_stream(self, stream_id: int, error_code: int = 0) -> int:
        """
        WebTransport ストリームを閉じる (nghttp3 に通知)。戻り値はリセットされたストリームが属するセッション ID。復元できない場合は -1
        """

    def reset_stream(self, stream_id: int, error_code: int = 0) -> None:
        """
        WebTransport ストリームをリセットする (nghttp3 に通知。データストリームは WT_APPLICATION_ERROR へリマップ)
        """

    def map_send_error_code(self, stream_id: int, error_code: int) -> int:
        """送信時のエラーコードをワイヤ用に変換する (データストリームは WT_APPLICATION_ERROR へリマップ)"""

    def close_session(self, session_id: int, error_code: int = 0, error_message: str = "") -> None:
        """WebTransport セッションを閉じる"""

    def next_event(self) -> Event | None:
        """次のイベントを取得"""

    def get_required_streams(self) -> list[tuple[str, bool]]:
        """必要な QUIC ストリーム ID のリストを取得"""

    def is_closed(self) -> bool:
        """接続が閉じられたか"""

    def is_webtransport_ready(self) -> bool:
        """対向 SETTINGS で WebTransport over HTTP/3 が有効か (クライアント用)"""

    def get_session_ids(self) -> list[int]:
        """確立されたセッション ID のリストを取得"""

    def get_session_streams(self, session_id: int) -> list[StreamInfo]:
        """セッションに属するストリームを取得"""

    def set_max_client_streams_bidi(self, max_streams: int) -> None:
        """クライアントからの双方向ストリームの最大数を設定 (単調増加のみ。減少値は ValueError)"""

    def _has_stream_buffer(self, stream_id: int) -> bool | None:
        """テスト専用: ストリームの送信バッファエントリの有無を確認"""

    def _has_pending_qpack_blocked_fin_stream(self, stream_id: int) -> bool | None:
        """テスト専用: QPACK ブロック中 fin の保留記録の有無を確認"""

    def _has_pending_headers(self, stream_id: int) -> bool | None:
        """テスト専用: 受信途中のヘッダーブロックのエントリの有無を確認"""

    def stream_writable(self, stream_id: int) -> int | None:
        """ストリームが書き込み可能か確認"""

    def stream_flushed(self, stream_id: int) -> int | None:
        """ストリームの全送信データが QUIC スタックに受け渡し済みか確認"""

    def stream_wt_session_id(self, stream_id: int) -> int | None:
        """ストリームが属する WebTransport セッション ID を取得"""

    def block_stream(self, stream_id: int) -> None:
        """ストリームの QUIC フロー制御ブロックを通知"""

    def unblock_stream(self, stream_id: int) -> bool:
        """ストリームの QUIC フロー制御ブロック解除を通知"""

    def max_concurrent_streams(self, n: int) -> None:
        """同時ストリーム数のヒントを設定"""
