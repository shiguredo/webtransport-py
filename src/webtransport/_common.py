"""高レベル API の層をまたいで共有する内部ヘルパー

`quic` / `h3` / `http3` の Client / Server が同一実装を持っていたものを
ここへ集約する。公開 API ではないため、利用者は参照しないこと。
"""

from __future__ import annotations

import os
from typing import Any, Protocol


class _Packet(Protocol):
    """送信先アドレスを持ち得るパケット (quic.Packet と同型)

    読み取り専用の property として宣言する。nanobind の公開プロパティは
    property として型付けされるため、可変属性として宣言すると実型が
    プロトコルを満たさなくなる。
    """

    @property
    def remote_host(self) -> str: ...

    @property
    def remote_port(self) -> int: ...


def normalize_addr(addr: tuple[Any, ...]) -> tuple[str, int]:
    """recvfrom / getsockname のアドレスを (str, int) に正規化する

    IPv6 の 4 要素タプル等が来ても先頭 2 要素だけを使う。port が int で
    ない場合は TypeError にする。

    Args:
        addr: recvfrom / getsockname が返したアドレス

    Returns:
        (host, port) の 2 要素タプル

    Raises:
        TypeError: port が int でない場合
    """
    host = addr[0]
    port = addr[1]
    if not isinstance(port, int):
        raise TypeError(f"expected port int, got {type(port).__name__}")
    return (str(host), port)


def destination_for_packet(
    packet: _Packet,
    remote_addr: tuple[str, int] | None,
    host: str,
    port: int,
) -> tuple[str, int]:
    """パケットの送信先アドレスを決める

    パケットにパス情報が埋まっている場合はそれを使う。無ければ解決済みの
    数値リモート、それも無ければ接続先のホスト名とポートにフォールバック
    する (C++ 側で解決されるが family 食い違いの余地が残る)。

    Args:
        packet: 送信するパケット
        remote_addr: 解決済みの数値リモートアドレス (未解決なら None)
        host: 接続先ホスト
        port: 接続先ポート

    Returns:
        (host, port) の 2 要素タプル
    """
    if packet.remote_host and packet.remote_port:
        return (packet.remote_host, packet.remote_port)
    if remote_addr is not None:
        return remote_addr
    return (host, port)


def parse_wt_url(url: str) -> tuple[str, int, str]:
    """WebTransport のエンドポイント URL を (host, port, path) にする

    `https://` のスキームは大文字小文字を問わず除去しない (入力は小文字を
    前提とする)。ポート省略時は 443 を使う。

    Args:
        url: `https://host:port/path` 形式の URL

    Returns:
        (host, port, path) の 3 要素タプル

    Raises:
        ValueError: ポートが整数でない場合
    """
    url = url.replace("https://", "")
    if "/" in url:
        host_port, path = url.split("/", 1)
        path = "/" + path
    else:
        host_port = url
        path = "/"

    if ":" in host_port:
        host, port_str = host_port.split(":")
        port = int(port_str)
    else:
        host = host_port
        port = 443

    return host, port, path


def validate_cert_key_files(certfile: str | None, keyfile: str | None) -> None:
    """証明書と鍵ファイルの存在と読み取り可能性を検証する

    None は未設定として検証を素通りする (接続時に既定動作になる)。
    起動後の削除・権限変更は run() 時の再 raise で検出する。

    Args:
        certfile: 証明書ファイルパス
        keyfile: 秘密鍵ファイルパス

    Raises:
        FileNotFoundError: ファイルが存在しない場合
        PermissionError: ファイルが読み取り不可の場合
    """
    for name, path in (("certfile", certfile), ("keyfile", keyfile)):
        if path is None:
            continue
        if not os.path.isfile(path):
            raise FileNotFoundError(f"{name} not found: {path}")
        if not os.access(path, os.R_OK):
            raise PermissionError(f"{name} is not readable: {path}")


def open_http3_uni_streams(quic_connection: Any) -> tuple[int, int, int]:
    """HTTP/3 の制御・QPACK エンコーダ・QPACK デコーダの単方向ストリームを開く

    3 本は常にこの順で開き、同じ順で bind する必要がある (RFC 9114 Section
    6.2)。開設自体はここに集約し、bind と失敗時の扱いは呼び出し側に残す
    (クライアントは開設失敗を無視し、サーバーは制御ストリームの失敗で打ち
    切るなど、層ごとに扱いが異なるため)。

    Args:
        quic_connection: ストリームを開く QUIC コネクション

    Returns:
        (制御, QPACK エンコーダ, QPACK デコーダ) のストリーム ID
    """
    control = quic_connection.open_stream(False)
    encoder = quic_connection.open_stream(False)
    decoder = quic_connection.open_stream(False)
    return control, encoder, decoder


def bind_http3_uni_streams(
    h3_connection: Any,
    control_stream_id: int,
    qpack_encoder_stream_id: int,
    qpack_decoder_stream_id: int,
) -> None:
    """HTTP/3 の制御・QPACK ストリームをバインドする

    Args:
        h3_connection: bind を受け持つ HTTP/3 または WebTransport セッション
        control_stream_id: 制御ストリーム ID
        qpack_encoder_stream_id: QPACK エンコーダストリーム ID
        qpack_decoder_stream_id: QPACK デコーダストリーム ID
    """
    h3_connection.bind_control_stream(control_stream_id)
    h3_connection.bind_qpack_encoder_stream(qpack_encoder_stream_id)
    h3_connection.bind_qpack_decoder_stream(qpack_decoder_stream_id)
