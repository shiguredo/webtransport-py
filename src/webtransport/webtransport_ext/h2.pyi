"""WebTransport over HTTP/2"""

import enum
from collections.abc import Sequence

class WtErrorCode(enum.Enum):
    """WebTransport over HTTP/2 のエラーコード"""

    WT_FLOW_CONTROL_ERROR = 80

    WT_STREAM_STATE_ERROR = 81

    WT_ERROR = 82

class CapsuleType(enum.Enum):
    """Capsule 種別"""

    DATAGRAM = 0

    PADDING = 420171064

    WT_RESET_STREAM = 420171065

    WT_STOP_SENDING = 420171066

    WT_STREAM = 420171068

    WT_STREAM_FIN = 420171067

    WT_MAX_DATA = 420171069

    WT_MAX_STREAM_DATA = 420171070

    WT_MAX_STREAMS_BIDI = 420171071

    WT_MAX_STREAMS_UNI = 420171072

    WT_DATA_BLOCKED = 420171073

    WT_STREAM_DATA_BLOCKED = 420171074

    WT_STREAMS_BLOCKED_BIDI = 420171075

    WT_STREAMS_BLOCKED_UNI = 420171076

    WT_CLOSE_SESSION = 10307

    WT_DRAIN_SESSION = 30894

class Config:
    """WebTransport over HTTP/2 設定"""

    def __init__(self) -> None: ...
    @property
    def initial_window_size(self) -> int: ...
    @initial_window_size.setter
    def initial_window_size(self, arg: int, /) -> None: ...
    @property
    def max_concurrent_streams(self) -> int: ...
    @max_concurrent_streams.setter
    def max_concurrent_streams(self, arg: int, /) -> None: ...
    @property
    def max_frame_size(self) -> int: ...
    @max_frame_size.setter
    def max_frame_size(self, arg: int, /) -> None: ...
    @property
    def max_header_list_size(self) -> int: ...
    @max_header_list_size.setter
    def max_header_list_size(self, arg: int, /) -> None: ...
    @property
    def is_server(self) -> bool: ...
    @is_server.setter
    def is_server(self, arg: bool, /) -> None: ...
    @property
    def wt_initial_max_data(self) -> int: ...
    @wt_initial_max_data.setter
    def wt_initial_max_data(self, arg: int, /) -> None: ...
    @property
    def wt_initial_max_stream_data(self) -> int: ...
    @wt_initial_max_stream_data.setter
    def wt_initial_max_stream_data(self, arg: int, /) -> None: ...
    @property
    def wt_initial_max_streams_bidi(self) -> int: ...
    @wt_initial_max_streams_bidi.setter
    def wt_initial_max_streams_bidi(self, arg: int, /) -> None: ...
    @property
    def wt_initial_max_streams_uni(self) -> int: ...
    @wt_initial_max_streams_uni.setter
    def wt_initial_max_streams_uni(self, arg: int, /) -> None: ...
    @property
    def wt_pre_accept_buffer_limit(self) -> int: ...
    @wt_pre_accept_buffer_limit.setter
    def wt_pre_accept_buffer_limit(self, arg: int, /) -> None: ...
    @property
    def wt_max_capsule_payload_size(self) -> int: ...
    @wt_max_capsule_payload_size.setter
    def wt_max_capsule_payload_size(self, arg: int, /) -> None: ...
    @property
    def allowed_origins(self) -> list[str]:
        """許可オリジンリスト (空なら Origin 検証を行わない)"""

    @allowed_origins.setter
    def allowed_origins(self, arg: Sequence[str], /) -> None: ...

class EventType(enum.Enum):
    """WebTransport over HTTP/2 イベント種別"""

    SESSION_READY = 0

    SESSION_CLOSED = 1

    SESSION_DRAINING = 2

    STREAM_DATA = 3

    STREAM_RESET = 4

    STOP_SENDING = 5

    DATAGRAM = 6

    ERROR = 7

    SESSION_REJECTED = 8

    GOAWAY = 9

class Event:
    """WebTransport over HTTP/2 イベント"""

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
    def fin(self) -> bool: ...
    @property
    def status_code(self) -> int:
        """SessionRejected 発火時の HTTP status code。他イベントでは 0 (不正値・パース失敗は 0 に丸められる)"""

    @property
    def headers(self) -> list[tuple[str, str]]:
        """SessionReady 発火時の受信 HTTP ヘッダー (疑似ヘッダー :status 等を含む)。他イベントでは空"""

    @property
    def last_stream_id(self) -> int:
        """GoAway 発火時の GOAWAY フレームの last_stream_id。他イベントでは 0"""

class Session:
    """WebTransport over HTTP/2 セッション"""

    @staticmethod
    def create_client(config: Config) -> Session:
        """クライアントセッションを作成 (Config の上限値超えは ValueError)"""

    @staticmethod
    def create_server(config: Config) -> Session:
        """サーバーセッションを作成 (Config の上限値超えは ValueError)"""

    def receive(self, data: bytes) -> int:
        """受信したデータを処理 (data が 1 MiB 超の場合は ValueError)"""

    def send(self) -> bytes | None:
        """送信すべきデータを取得"""

    def connect(self, url: str, origin: str = "") -> int:
        """WebTransport セッションを開始 (クライアント用)"""

    def is_webtransport_ready(self) -> bool:
        """対向 SETTINGS で WebTransport over HTTP/2 が有効か"""

    def _test_pending_header_count(self, stream_id: int) -> int | None:
        """テスト専用: 受信途中のヘッダーブロックのヘッダー数"""

    def _test_unfinished_capsule_bytes(self, session_id: int) -> int | None:
        """テスト専用: 未完成カプセルとして保持中のバイト数"""

    def accept_session(self, session_id: int) -> bool:
        """WebTransport セッションを受理 (サーバー用)"""

    def reject_session(self, session_id: int, status_code: int) -> None:
        """
        WebTransport セッションを拒否 (サーバー用。session_id は正のストリーム ID のみ (0 以下は ValueError。クライアントセッションでは no-op)。status_code は 200-599 (実質 300-599 用)。1xx と 3 桁未満・4 桁以上・600 以上は ValueError。405 の場合は Allow: CONNECT を応答に含める。応答の submit / 送出失敗は ERROR イベントを発火する)
        """

    def open_stream(self, session_id: int, is_unidirectional: bool) -> int:
        """WebTransport ストリームを開く"""

    def send_stream_data(
        self, session_id: int, stream_id: int, data: bytes, fin: bool = False
    ) -> None:
        """WebTransport ストリームにデータを送信 (data が 1 MiB 超の場合は ValueError)"""

    def reset_stream(self, session_id: int, stream_id: int, error_code: int) -> None:
        """WebTransport ストリームをリセット (stream_id が 2^62 以上の場合は ValueError)"""

    def stop_sending(self, session_id: int, stream_id: int, error_code: int) -> None:
        """送信停止を要求 (stream_id が 2^62 以上の場合は ValueError)"""

    def send_datagram(self, session_id: int, data: bytes) -> None:
        """データグラムを送信 (data が 1 MiB 超の場合は ValueError)"""

    def close_session(self, session_id: int, error_code: int = 0, error_message: str = "") -> None:
        """WebTransport セッションを閉じる"""

    def drain_session(self, session_id: int) -> None:
        """セッションのドレインを開始"""

    def next_event(self) -> Event | None:
        """次のイベントを取得"""

    def want_write(self) -> bool:
        """送信待ちデータがあるか"""

    def is_closed(self) -> bool:
        """接続が閉じられたか"""

    def get_send_credit(self, session_id: int) -> int:
        """セッションレベルの送信可能残量を返す (観測専用)"""

    def get_session_ids(self) -> list[int]:
        """確立されたセッション ID のリストを取得"""

    def get_stream_ids(self, session_id: int) -> list[int]:
        """セッションに属するストリーム ID を取得"""
