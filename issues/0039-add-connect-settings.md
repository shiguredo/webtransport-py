# 高レベル QUIC クライアントに connect のタイムアウトと max_datagram_frame_size を追加する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-connect-settings
- Polished: YYYY-MM-DD
- Reporter: @voluntas

## 目的

高レベル `Client` に ngtcp2-py 互換の接続設定 API (`connect` のタイムアウトと `max_datagram_frame_size`) を追加する。

## 現状

- webtransport-py の `Client.connect()` は while ループでハンドシェイク完了を無制限に待ち、ハンドシェイクが進まない場合に無限にブロックする
- ngtcp2-py は `connect(timeout=10.0)` でハンドシェイクを打ち切る
- webtransport-py の `Client` コンストラクタに `max_datagram_frame_size` が無い。低レベル `Config` には `max_datagram_frame_size` / `enable_datagram` が既にあるが、`Client.connect()` は `enable_datagram` を設定していない
- sora-quic の `test_ngtcp2_datagram.py` が `max_datagram_frame_size=1200` を指定して DATAGRAM を送受信している

## 設計方針

- `connect(timeout: float)` 引数を追加し、ハンドシェイク完了までをタイムアウトで打ち切る (期限内に確立できない場合は `False` を返す)
- コンストラクタに `max_datagram_frame_size: int = 0` を追加し、`connect()` で低レベル `Config.enable_datagram` と `Config.max_datagram_frame_size` へ反映する (ngtcp2-py と同じく 0 のあいだは DATAGRAM を広告しない)

## 完了条件

- `connect(timeout=...)` がハンドシェイク未完了のまま期限に達した場合に `False` を返して終了する
- `max_datagram_frame_size` を指定すると DATAGRAM を送受信できる (指定しない場合は DATAGRAM を広告しない)
- テストを追加する (connect のタイムアウト / DATAGRAM の広告有無)

## 解決方法

(実装時に追記する)