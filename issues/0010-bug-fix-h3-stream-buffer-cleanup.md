# WebTransport over HTTP/3 の close_stream / reset_stream が送信バッファを削除しないのを修正する

- Created: 2026-08-02
- Completed: YYYY-MM-DD
- Branch: feature/fix-h3-stream-buffer-cleanup
- Polished: {YYYY-MM-DD}

## 目的

リセットされたストリームの未送信データが接続終了までメモリに残る問題を修正する。リセット後のストリームには ACK が来ないため、送信バッファが削除されることはなく、接続単位で有界とはいえ無駄にメモリを保持し続ける。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::close_stream` と `H3Session::reset_stream` は `stream_info_` からストリーム情報を削除するが、`stream_buffers_` は削除しない
- 送信バッファの削除は `acked_stream_data_cb` (ACK 受信時) にのみ行われ、RESET 後のストリームには ACK が来ないため未 ACK データが残り続ける
- 対称に、`src/bindings/quic.cpp` の `QuicConnection::reset_stream` は `stream_buffers_` から削除しており、h3 側だけ非対称になっている

## 設計方針

- `H3Session::close_stream` / `H3Session::reset_stream` で `stream_buffers_` からもストリームのエントリを削除する

## 完了条件

- リセットされたストリームの送信バッファが削除される
- モックなしのテストで検証できる (送信後にリセットし、バッファが解放されることと接続が維持されることを確認する)
