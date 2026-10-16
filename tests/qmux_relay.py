"""QMux の CONNECTION_CLOSE を実 TCP 上で観測するテスト用リレー

クライアントとサーバーの間に挟まる 1 対 1 の TCP リレーと、QMux のレコードから
CONNECTION_CLOSE を取り出すパーサを提供する。クライアントの接続先には
リレーのアドレスを渡し、リレーは転送先 (実際のサーバー) へ接続する。リレーは
バイト列を改変せずに中継するため、ワイヤに載った終了コードと理由をそのまま
観測できる。

仕様の根拠:
    - QMux のレコードは varint の Size と、その長さの Frames から成る
      (draft-ietf-quic-qmux-02 Section 3.2)。Frames は QUIC v1 のフレーム列で、
      レコードは自己区切り (self-delimiting) である
    - CONNECTION_CLOSE の形は QUIC v1 のフレーム (RFC 9000 Section 19.19)。
      0x1c は error code + frame type + reason length + reason、0x1d は
      error code + reason length + reason
    - 終了コードと reason length、フレーム種別は QUIC の varint
      (RFC 9000 Section 16、フレーム種別は同 Section 12.4)
    - dwnx は `dwnx_ccerr_set_application_error` で閉じるため 0x1d を送る。
      `dwnx_conn_write_connection_close` は CONNECTION_CLOSE だけを含む
      1 レコードを書く

いずれも将来改訂される可能性がある。

プロダクトコードではなくテスト専用のヘルパーであり、モックやスタブではなく
実 TCP の中継で観測する。
"""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal, Self

__all__ = [
    "ConnectionClose",
    "Direction",
    "QmuxRelay",
    "parse_connection_close",
    "split_records",
]

logger = logging.getLogger(__name__)

# 転送方向。クライアントからサーバーへ流れるバイト列が "client_to_server"、
# サーバーからクライアントへ流れるバイト列が "server_to_client"
Direction = Literal["client_to_server", "server_to_client"]

# リレーが待ち受けるホスト。テストは常にローカルホストで完結させる
_LISTEN_HOST = "127.0.0.1"

# 一度に読み取る最大バイト数。QMux の 1 レコードの最大長 (16 KiB 程度) より
# 十分大きく取る
_READ_SIZE = 65536

# CONNECTION_CLOSE のフレーム種別 (RFC 9000 Section 19.19)
_FRAME_TYPE_CONNECTION_CLOSE_TRANSPORT = 0x1C
_FRAME_TYPE_CONNECTION_CLOSE_APPLICATION = 0x1D

# 相手が先に切れたときにソケット操作が送出する例外。リレーはテスト専用の
# ヘルパーなので、これらは転送の終了として扱う。プログラム上の誤りまで
# 隠さないよう、対象を接続断の例外に限定する
_TRANSPORT_ERRORS = (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)


@dataclass(frozen=True)
class ConnectionClose:
    """QMux のレコードから取り出した CONNECTION_CLOSE

    Attributes:
        frame_type: フレーム種別。0x1c (トランスポート) か 0x1d (アプリケーション)
        error_code: 終了コード。0x1d の場合はアプリケーションエラーコード
        reason: 理由の文字列。空の場合もある
        is_application: 0x1d (アプリケーション) かどうか
    """

    frame_type: int
    error_code: int
    reason: str
    is_application: bool


def _decode_varint(data: bytes | bytearray, offset: int) -> tuple[int, int] | None:
    """QUIC の varint を 1 つ読み出す

    RFC 9000 Section 16 のとおり、先頭 2 ビットがエンコード長 (1 / 2 / 4 / 8
    バイト) を表し、残りのビットがネットワークバイトオーダーの値になる。
    フレーム種別以外は最短エンコードでなくてもよい (同 Section 16)。

    Args:
        data: 読み出すバイト列
        offset: 読み出しを始める位置

    Returns:
        (値, エンコードに使われたバイト数)。バイト列が足りない場合は None
    """
    if offset >= len(data):
        return None
    first = data[offset]
    length = 1 << (first >> 6)
    if len(data) - offset < length:
        return None
    value = first & 0x3F
    for index in range(1, length):
        value = (value << 8) | data[offset + index]
    return value, length


def _read_record(data: bytes | bytearray, offset: int) -> bytes | None:
    """offset から QMux レコードを 1 つ取り出す

    QMux レコードは varint の Size と、その長さの Frames から成る
    (draft-ietf-quic-qmux-02 Section 3.2)。Size は Frames の長さを表し、
    Size 自身は含まない。

    Args:
        data: 読み出すバイト列
        offset: 読み出しを始める位置

    Returns:
        レコード全体 (Size + Frames)。Size が読めない、または Frames が
        まだ揃っていない場合は None
    """
    decoded_size = _decode_varint(data, offset)
    if decoded_size is None:
        return None
    size, size_length = decoded_size
    end = offset + size_length + size
    if len(data) < end:
        return None
    return bytes(data[offset:end])


def split_records(data: bytes) -> list[bytes]:
    """バイト列を QMux レコードの列に分割する

    完成したレコードだけを返し、末尾に残った途中のレコードは捨てる。途中の
    レコードの続きが届いた場合は、続きを含むバイト列を改めて渡す。

    Args:
        data: 分割するバイト列

    Returns:
        完成したレコード (Size + Frames) のリスト。受信順に並ぶ
    """
    records: list[bytes] = []
    offset = 0
    while (record := _read_record(data, offset)) is not None:
        records.append(record)
        offset += len(record)
    return records


def parse_connection_close(record: bytes) -> ConnectionClose | None:
    """QMux レコードから CONNECTION_CLOSE を取り出す

    Frames の先頭にあるフレームだけを調べる。dwnx は CONNECTION_CLOSE だけを
    含む 1 レコードを書くため、通常はこれで足りる。次の場合は例外を送出せず
    None を返す。

        - Size が読めない、または Size と実際の長さが食い違う
          (途中で切れたレコード)
        - 先頭のフレームが CONNECTION_CLOSE 以外 (STREAM など)
        - CONNECTION_CLOSE のフィールドが途中で切れている
        - CONNECTION_CLOSE の後ろに別のフレームが続いている

    Args:
        record: QMux レコード全体 (varint の Size + Frames)

    Returns:
        取り出した CONNECTION_CLOSE。取り出せない場合は None
    """
    # Size を読み、Frames の範囲を確定する。Size と実際の長さが食い違う場合は
    # 途中で切れたレコードとみなす
    decoded_size = _decode_varint(record, 0)
    if decoded_size is None:
        return None
    size, size_length = decoded_size
    frames = record[size_length:]
    if len(frames) != size:
        return None

    # フレーム種別も varint (RFC 9000 Section 12.4)
    decoded_frame_type = _decode_varint(frames, 0)
    if decoded_frame_type is None:
        return None
    frame_type, offset = decoded_frame_type
    if frame_type not in (
        _FRAME_TYPE_CONNECTION_CLOSE_TRANSPORT,
        _FRAME_TYPE_CONNECTION_CLOSE_APPLICATION,
    ):
        return None
    is_application = frame_type == _FRAME_TYPE_CONNECTION_CLOSE_APPLICATION

    # Error Code (varint)
    decoded_error_code = _decode_varint(frames, offset)
    if decoded_error_code is None:
        return None
    error_code, error_code_length = decoded_error_code
    offset += error_code_length

    # 0x1c だけが、エラーを起こしたフレーム種別 (varint) を持つ。
    # 0x1d はこのフィールドを持たない (RFC 9000 Section 19.19)
    if not is_application:
        decoded_trigger = _decode_varint(frames, offset)
        if decoded_trigger is None:
            return None
        offset += decoded_trigger[1]

    # Reason Phrase Length (varint) と Reason Phrase
    decoded_reason_length = _decode_varint(frames, offset)
    if decoded_reason_length is None:
        return None
    reason_length, reason_length_size = decoded_reason_length
    offset += reason_length_size
    # Reason Phrase の長さが合わない場合は、後ろに別のフレームが続いているか
    # 途中で切れている
    if len(frames) - offset != reason_length:
        return None
    # Reason Phrase は UTF-8 であることが推奨されている (RFC 9000 Section 19.19)。
    # テスト用のパーサなので、不正なバイト列でも例外にせず置換文字にする
    reason = frames[offset:].decode("utf-8", errors="replace")

    return ConnectionClose(
        frame_type=frame_type,
        error_code=error_code,
        reason=reason,
        is_application=is_application,
    )


def _half_close(writer: asyncio.StreamWriter) -> None:
    """送信側だけを閉じる

    書き込みバッファのフラッシュ後に FIN が送られるため、転送すべきバイト列を
    転送し終えてから相手に EOF を届けられる。受信側は開いたままにして、逆向きの
    転送 (サーバーが返す CONNECTION_CLOSE など) を打ち切らないようにする。
    """
    if writer.is_closing():
        return
    if writer.can_write_eof():
        try:
            writer.write_eof()
            return
        except OSError as exc:
            # 相手がすでに接続を閉じていると shutdown(SHUT_WR) が失敗する
            # (macOS では ENOTCONN)。転送の終了処理を止めないよう、閉じる
            # 処理に切り替える
            logger.debug("リレーの write_eof に失敗した: %s", exc)
    writer.close()


async def _close_writer(writer: asyncio.StreamWriter | None) -> None:
    """接続を閉じて、閉じ終わるまで待つ

    Args:
        writer: 閉じる接続。None の場合は何もしない
    """
    if writer is None:
        return
    if not writer.is_closing():
        writer.close()
    try:
        await writer.wait_closed()
    except OSError as exc:
        # 接続を閉じる処理の失敗でテストを落とさない。相手がすでに切っている
        # 場合の ENOTCONN などもここに含まれる
        logger.debug("リレーの接続はすでに切れていた: %s", exc)


class QmuxRelay:
    """クライアントとサーバーの間に挟まる 1 対 1 の TCP リレー

    `start` で起動し、転送先 (実際のサーバー) のアドレスを渡す。待ち受けは
    127.0.0.1 の空きポートで行われ、`port` で取得できる。クライアントの接続先に
    は `("127.0.0.1", relay.port)` を渡す。

    リレーは受け取ったバイト列を改変せずにそのまま転送し、方向ごとに
    受け取ったバイト列を `client_to_server_bytes` / `server_to_client_bytes` に
    保持する。QMux のレコード境界で CONNECTION_CLOSE を探し、観測できたら
    `wait_for_connection_close` がイベントで待ち手を起こす。

    クライアントが先に EOF で切れた場合も、読み終えたバイト列を転送し終えて
    から相手側の送信を閉じる。

    Usage:
        relay = await QmuxRelay.start("127.0.0.1", server.actual_port)
        client = qmux.Client("127.0.0.1", relay.port)
        await client.connect()
        await client.close()
        connection_close = await relay.wait_for_connection_close()
        await relay.stop()
    """

    def __init__(self, host: str, port: int) -> None:
        """リレーを作る

        `start` を使う場合はこのコンストラクタを直接呼ぶ必要はない。

        Args:
            host: 転送先 (実際のサーバー) のホスト
            port: 転送先 (実際のサーバー) のポート
        """
        self._upstream_host = host
        self._upstream_port = port
        # 待ち受けと、転送先・クライアントそれぞれの接続 (未接続なら None)
        self._listener: asyncio.Server | None = None
        self._server_reader: asyncio.StreamReader | None = None
        self._server_writer: asyncio.StreamWriter | None = None
        self._client_writer: asyncio.StreamWriter | None = None
        # 方向ごとの転送タスク
        self._pumps: dict[Direction, asyncio.Task[None] | None] = {
            "client_to_server": None,
            "server_to_client": None,
        }
        # 方向ごとに、リレーが受け取った全バイト列と、レコード境界を走査した位置
        self._received: dict[Direction, bytearray] = {
            "client_to_server": bytearray(),
            "server_to_client": bytearray(),
        }
        self._scan_offsets: dict[Direction, int] = {
            "client_to_server": 0,
            "server_to_client": 0,
        }
        # 方向ごとに観測した CONNECTION_CLOSE (最初の 1 つだけ保持する) と、
        # 観測を待ち手へ知らせるイベント。固定 sleep でのポーリングを避けるため、
        # 待機はこのイベントで行う
        self._connection_closes: dict[Direction, ConnectionClose | None] = {
            "client_to_server": None,
            "server_to_client": None,
        }
        self._events: dict[Direction, asyncio.Event] = {
            "client_to_server": asyncio.Event(),
            "server_to_client": asyncio.Event(),
        }
        # 方向ごとの転送タスクが終わったかどうか
        self._finished: dict[Direction, bool] = {
            "client_to_server": False,
            "server_to_client": False,
        }
        self._stopped = False

    @classmethod
    async def start(cls, host: str, port: int) -> Self:
        """リレーを起動する

        待ち受けは 127.0.0.1 の空きポートで行い、`port` で取得できる。

        Args:
            host: 転送先 (実際のサーバー) のホスト
            port: 転送先 (実際のサーバー) のポート

        Returns:
            起動済みのリレー

        Raises:
            OSError: 転送先に接続できない、または待ち受けに失敗した場合
        """
        relay = cls(host, port)
        await relay._start()
        return relay

    @property
    def port(self) -> int:
        """待ち受けているポート

        Raises:
            RuntimeError: 起動前、または停止後に参照した場合
        """
        listener = self._listener
        if listener is None:
            raise RuntimeError("relay is not started")
        sockets = listener.sockets
        if not sockets:
            raise RuntimeError("relay is not listening")
        return int(sockets[0].getsockname()[1])

    @property
    def client_to_server_bytes(self) -> bytes:
        """クライアントから受け取ったバイト列 (サーバーへ転送するもの)"""
        return bytes(self._received["client_to_server"])

    @property
    def server_to_client_bytes(self) -> bytes:
        """サーバーから受け取ったバイト列 (クライアントへ転送するもの)"""
        return bytes(self._received["server_to_client"])

    async def wait_for_connection_close(
        self,
        timeout: float = 10.0,
        *,
        direction: Direction = "client_to_server",
    ) -> ConnectionClose:
        """指定した方向を流れる CONNECTION_CLOSE を待つ

        イベント駆動で待つため、固定 sleep でのポーリングは行わない。すでに
        観測済みの場合はすぐに返る。観測するのはリレーに届いた時点であり、
        相手側へ転送し終えた時点ではない (バイト列は転送前に記録する)。

        Args:
            timeout: 待つ最大秒数
            direction: 待つ方向。"client_to_server" はクライアントが送る
                CONNECTION_CLOSE、"server_to_client" はサーバーが送る
                CONNECTION_CLOSE を待つ

        Returns:
            観測した CONNECTION_CLOSE

        Raises:
            TimeoutError: timeout 秒以内に観測できなかった場合
        """
        connection_close = self._connection_closes[direction]
        if connection_close is not None:
            return connection_close
        try:
            await asyncio.wait_for(self._events[direction].wait(), timeout)
        except TimeoutError as exc:
            raise TimeoutError(
                f"no CONNECTION_CLOSE observed in direction {direction} "
                f"within {timeout} seconds after "
                f"{len(self._received[direction])} bytes"
            ) from exc
        connection_close = self._connection_closes[direction]
        if connection_close is None:
            # イベントは CONNECTION_CLOSE を保持してから立てるため、ここには来ない
            raise RuntimeError(f"CONNECTION_CLOSE event was set without a frame in {direction}")
        return connection_close

    async def stop(self) -> None:
        """リレーを停止する

        転送中のタスクを取り消し、待ち受けと両側の接続を閉じる。2 回以上
        呼んでも何もしない。
        """
        if self._stopped:
            return
        self._stopped = True
        # 先に新規の接続の受け付けを止める。wait_closed() は受け付けた接続が
        # すべて閉じるまで待つため、待ち合わせは接続を閉じたあとに行う
        listener = self._listener
        self._listener = None
        if listener is not None:
            listener.close()
        for task in self._pumps.values():
            if task is not None:
                task.cancel()
        try:
            for task in self._pumps.values():
                if task is not None:
                    with suppress(asyncio.CancelledError):
                        await task
        finally:
            # 転送タスクが想定外の例外で終わっていても、接続は必ず閉じる
            self._pumps = {"client_to_server": None, "server_to_client": None}
            await self._close_connections()
            if listener is not None:
                await listener.wait_closed()

    async def _start(self) -> None:
        """転送先へ接続し、クライアントの待ち受けを始める

        Raises:
            RuntimeError: すでに起動している場合
            OSError: 転送先に接続できない、または待ち受けに失敗した場合
        """
        if self._listener is not None:
            raise RuntimeError("relay has already been started")
        # 転送先へ先に接続する。接続できない場合は待ち受けを始めずに送出する
        server_reader, server_writer = await asyncio.open_connection(
            self._upstream_host, self._upstream_port
        )
        try:
            listener = await asyncio.start_server(self._on_client_connected, _LISTEN_HOST, 0)
        except OSError:
            # 待ち受けに失敗したら転送先の接続を残さない
            await _close_writer(server_writer)
            raise
        self._server_reader = server_reader
        self._server_writer = server_writer
        self._listener = listener

    async def _on_client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """クライアントの接続を受け入れ、双方向の転送を始める"""
        if self._stopped:
            await _close_writer(writer)
            return
        if self._client_writer is not None:
            # 1 対 1 のリレーなので 2 本目の接続は受け入れない
            logger.warning(
                "リレーは 2 本目のクライアント接続を受け入れない: peername=%s",
                writer.get_extra_info("peername"),
            )
            await _close_writer(writer)
            return
        server_reader = self._server_reader
        server_writer = self._server_writer
        if server_reader is None or server_writer is None:
            raise RuntimeError("relay is not started")
        self._client_writer = writer
        # クライアント → サーバーと、サーバー → クライアントの 2 方向を
        # それぞれ独立したタスクで転送する
        self._pumps["client_to_server"] = asyncio.create_task(
            self._pump("client_to_server", reader, server_writer)
        )
        self._pumps["server_to_client"] = asyncio.create_task(
            self._pump("server_to_client", server_reader, writer)
        )

    async def _pump(
        self,
        direction: Direction,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """片方向のバイト列を転送し続ける

        reader が EOF になったら、読み終えたバイト列をすべて転送し終えてから
        writer の送信側を閉じる。
        """
        try:
            while True:
                data = await reader.read(_READ_SIZE)
                if not data:
                    # 相手が送信側を閉じた
                    break
                self._observe(direction, data)
                writer.write(data)
                await writer.drain()
        except _TRANSPORT_ERRORS as exc:
            # 相手が先に切れた場合は転送の終了として扱う
            logger.debug("リレーの %s 転送が切れた: %s", direction, exc)
        finally:
            self._finished[direction] = True
            _half_close(writer)
            if all(self._finished.values()):
                # 両方向とも終わったので接続を片付ける
                await self._close_connections()

    def _observe(self, direction: Direction, data: bytes) -> None:
        """受け取ったバイト列を記録し、完成したレコードから CONNECTION_CLOSE を探す

        記録は相手側への転送より先に行う。そのため
        `client_to_server_bytes` / `server_to_client_bytes` には、リレーが
        受け取ったバイト列がそのまま入る。
        """
        received = self._received[direction]
        received.extend(data)
        offset = self._scan_offsets[direction]
        while (record := _read_record(received, offset)) is not None:
            offset += len(record)
            if self._connection_closes[direction] is not None:
                # 最初に観測した CONNECTION_CLOSE だけを採用する
                continue
            connection_close = parse_connection_close(record)
            if connection_close is None:
                continue
            self._connection_closes[direction] = connection_close
            logger.info(
                "リレーが %s で CONNECTION_CLOSE を観測した: "
                "frame_type=0x%02x error_code=%d reason=%r",
                direction,
                connection_close.frame_type,
                connection_close.error_code,
                connection_close.reason,
            )
            # 固定 sleep でのポーリングを避けるため、イベントで待ち手を起こす
            self._events[direction].set()
        self._scan_offsets[direction] = offset

    async def _close_connections(self) -> None:
        """両側の接続を閉じる

        2 つの転送タスクと `stop` の両方から呼ばれる。接続の参照は await する
        前に None にするため、同時に呼ばれても 2 回目は何もしない。
        """
        client_writer = self._client_writer
        server_writer = self._server_writer
        self._client_writer = None
        self._server_reader = None
        self._server_writer = None
        await _close_writer(client_writer)
        await _close_writer(server_writer)
