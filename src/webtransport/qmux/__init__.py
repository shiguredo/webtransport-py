"""QMux (dwnx) の Sans-IO API

QMux は TLS/TCP のような双方向バイトストリーム上で、QUIC v1 相当のストリームと
多重化を提供するプロトコル (draft-ietf-quic-qmux)。QUIC のパケット層を持たず、
レコード (varint の長さ + QUIC フレーム列) をバイト列として入出力する。

提供するもの:
    - Config: 接続設定
    - Connection: QMux 接続
    - Event: イベント
    - EventType: イベント種別
    - get_version: dwnx のバージョン文字列

Usage:
    from webtransport import qmux

    config = qmux.Config()
    client = qmux.Connection.create_client(config)
    server = qmux.Connection.create_server(config)

    # 受け取ったバイト列を渡し、送信すべきレコードを取り出す
    client.receive(record_from_peer)
    record = client.pending_record

接続はトランスポートパラメータを帯域内で交換するため、TLS が無くても 2 つの
接続をバイト列で直結すればハンドシェイクできる。
"""

from webtransport.webtransport_ext.qmux import (
    Config,
    Connection,
    Event,
    EventType,
    get_version,
)

__all__ = [
    "Config",
    "Connection",
    "Event",
    "EventType",
    "get_version",
]
