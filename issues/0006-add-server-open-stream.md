# WebTransport over HTTP/3 サーバーにストリームを開く API を追加する

- Created: 2026-08-01
- Completed: YYYY-MM-DD
- Branch: feature/add-server-open-stream
- Polished: YYYY-MM-DD

## 目的

サーバーからクライアントへの一方的なストリーム送信 (server-initiated stream) を高レベル API で可能にする。現在は低レベル API でしか実現できず、高レベル `Server` ではサーバー push ができない。

## 現状

- `src/webtransport/h3/server.py` の `Server` は `send_stream_data` / `reset_stream` / `close_stream` / `send_datagram` を持つが、ストリームを新規に開く API が無い
- 低レベル API には `webtransport.h3.Session` の `open_stream` (`src/bindings/webtransport_h3.cpp`) と `quic.Connection` の `open_stream` (`src/bindings/quic.cpp`) が存在する

## 設計方針

- `Server` に `open_stream` を追加し、返された stream_id に対して `send_stream_data` で送信できるようにする
- サーバーからクライアントへの単方向ストリームの送信を想定する
- 低レベル API の既存実装を利用する

## 完了条件

- 高レベル API でサーバーからクライアントへの単方向ストリーム送信ができる
- モックなしの e2e テストでクライアント側のストリーム受信を検証できる
