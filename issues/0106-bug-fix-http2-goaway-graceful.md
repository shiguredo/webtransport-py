# HTTP/2 の GOAWAY 受信で進行中ストリームの処理が止まる問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http2-goaway-graceful
- Polished: {YYYY-MM-DD}

## 目的

RFC 9113 Section 6.8 が定義する GOAWAY の graceful shutdown (既存ストリームの処理を完了させながら新規ストリーム受付を止める) に反し、GOAWAY 受信直後に接続が closed になる問題を修正する。現在は進行中ストリームのレスポンス flush が止まり、データが失われる。

## 現状

- `src/bindings/http2.cpp` の `on_frame_recv_callback` は GOAWAY 受信で `closed_ = true` にし、以後 `receive()` は 0 を返し `send()` は nullopt を返す
- 送信キューに積んだレスポンス (HEADERS + DATA) が GOAWAY 受信後に一切 flush されない (実測確認済み)
- `tests/test_http2.py` の `test_http2_closed_connection_guards` がこの挙動を仕様として固定している

## 設計方針

- GOAWAY 受信後も既存ストリームの送受信を続行できるようにし、新規ストリームの送信のみを抑止する
- graceful shutdown のテストを追加する

## 完了条件

- GOAWAY 受信後も進行中ストリームのデータ処理と送信 flush が完了する
- 新規ストリームの開始が抑止される
- テストが追加される
