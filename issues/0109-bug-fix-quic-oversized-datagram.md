# QUIC の過大データグラムが送信キューを永久に塞ぐ問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-quic-oversized-datagram
- Polished: {YYYY-MM-DD}

## 目的

RFC 9221 の「max_datagram_frame_size を超えるデータグラムを送ってはならない」に反し、過大なデータグラムを送信キューに積むと後続の全データグラムが永久に送出されない問題を修正する。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::send_datagram` はサイズ検証なしでキューに積む
- `ngtcp2_conn_writev_datagram` はピアの max_datagram_frame_size 超過で INVALID_ARGUMENT を返すため、書き込みループが break し、過大データグラムが先頭に残り続ける
- 以降の全データグラムが永遠に送信されない (head-of-line ブロック)

## 設計方針

- エンキュー時にピアの max_datagram_frame_size を確認し、超過するデータグラムを破棄またはエラー通知する
- 過大データグラムのテストを追加する

## 完了条件

- 過大なデータグラムが送信キューに残らず、後続のデータグラムが送出される
- テストが追加される
