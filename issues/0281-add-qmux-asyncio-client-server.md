# QMux の asyncio クライアントとサーバーを追加する

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/add-qmux-asyncio-client-server
- Polished: {YYYY-MM-DD}

## 目的

QMux v1 (draft-ietf-quic-qmux-02) を asyncio から使えるようにする。0279 で Sans-IO API (`webtransport.qmux.Config` / `Connection` / `Event`) を追加したが、ソケットの読み書き・タイマー・イベント配布は利用者の責務のままである。QMux は UDP が通らない経路で QUIC 相当の多重化を使うための polyfill であり、TCP をつないでストリームを流すだけの用途でも QUIC / HTTP/3 / HTTP/2 と同じ asyncio 層があると実用的になる。

## 現状

- `src/webtransport/qmux/__init__.py` は `webtransport_ext.qmux` の `Config` / `Connection` / `Event` / `EventType` / `get_version` を再輸出するだけである
- `Connection` は `receive(data) -> int` と `pending_record -> bytes | None` を持つ Sans-IO API で、I/O を持たない
- `webtransport.quic` / `http3` / `http2` と `_h2_client.py` / `_h3_client.py` には asyncio の Client / Server があるが、`webtransport.qmux` には無い
- DATAGRAM は dwnx が未実装であり、`webtransport_ext.qmux` にも対応する API が無い

## 設計方針

- `webtransport.qmux.Client` と `webtransport.qmux.Server` を追加する。QMux は HTTP ではないため `HTTPVersion` には載せず、統一 API (`webtransport.Client` / `Server`) にも統合しない
- トランスポートは TCP とし、`ssl` に `SSLContext` を渡せば TLS 上で動かす。draft Section 3 は「順序どおりで信頼できる双方向バイトストリームを提供する任意のトランスポート」を要求するだけで、平文 TCP や UNIX ソケットも想定内である
- TLS を使うときは `alpn_protocols` の指定を必須にする。draft Section 8.1 は「QMux over TLS でアプリケーションプロトコルを動かすときは ALPN で合意すること (MUST)」と定めている
- 接続先は `host` と `port` の引数で指定する。QMux にはパスやヘッダーが無く、h2 のような URL を持たないためである
- コールバックは統一 Server と同じ形にし、サーバー側は `Session` ハンドル経由でストリームを操作する
- `webtransport.qmux.exceptions` を追加し、接続とプロトコルの失敗を層の例外として送出する
- DATAGRAM は dwnx が未実装のため対象外とする

## 完了条件

- `webtransport.qmux.Client(host, port)` で TCP 接続し、`open_stream` / `send_stream_data` / `on_stream_data` でデータをやり取りできる
- `webtransport.qmux.Server(host, port)` が接続を受け付け、`Session` ハンドルからストリームを開いて送信できる
- `ssl` を渡すと TLS 上で動き、`alpn_protocols` を省略するとエラーになる
- テストが実ソケット (TCP) を使い、モックを使っていない

## 解決方法

（実装後に追記）
