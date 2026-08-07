# QUIC クライアントの _send_pending が送信パケット数を返すようにする

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-send-pending-count
- Polished: YYYY-MM-DD
- Reporter: @voluntas

## 目的

`_send_pending` が送信したパケット数を返すようにし、CONNECTION_CLOSE の送信検証を可能にする。

## 現状

- webtransport-py の高レベル `Client._send_pending()` は送信待ちパケットを 1 つ送出して `None` を返す
- sora-quic の `test_ngtcp2_close_sends_connection_close_packet` は `client._connection.close()` を呼んだ後に `sent_packets = await client._send_pending()` の戻り値が 0 より大きいことを検証する
- ngtcp2-py の `_send_pending(max_packets=None)` は送信したパケット数を返す

## 設計方針

- `_send_pending()` が送信したパケット数を返すようにする (パケットを送信しない場合は 0)
- 既存の送信挙動 (1 呼び出しあたり 1 パケットの制約) は変更しない。ACK なしで連続 drain すると戻ってこなくなる制約は維持する

## 完了条件

- `_send_pending()` が送信パケット数を返す
- close() 後に `_connection.send()` が生成した CONNECTION_CLOSE パケットを `_send_pending()` が送出し、戻り値で確認できる
- 既存の全テストが通る

## 解決方法

(実装時に追記する)