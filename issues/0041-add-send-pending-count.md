# 高レベル QUIC クライアントの _send_pending が送信パケット数を返すようにする

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-send-pending-count
- Polished: 2026-08-09
- Reporter: @voluntas

## 目的

`_send_pending` が送信したパケット数を返すようにし、CONNECTION_CLOSE の送信検証を可能にする。

## 現状

- webtransport-py の高レベル `Client._send_pending()` (src/webtransport/quic/client.py) は送信待ちパケットを 1 つ送出して `None` を返す
- sora-quic の `test_ngtcp2_close_sends_connection_close_packet` は `client._connection.close()` を呼んだ後に `sent_packets = await client._send_pending()` の戻り値が 0 より大きいことを検証する
- ngtcp2-py の `_send_pending(max_packets=None)` は送信したパケット数を返す

## 設計方針

- `_send_pending()` が送信したパケット数を返すようにする (パケットを送信しない場合は 0)。1 呼び出しあたり 1 パケットの制約は変更しないため、戻り値は 0 か 1 になる
- 変更対象は src/webtransport/quic/client.py の高レベル `Client._send_pending()` (シグネチャの変更と docstring の Returns 追記を含む) と tests/test_e2e_quic.py へのテスト追加のみ。既存の 1 パケット制約の理由づけ (「ACK なしで連続 drain すると戻ってこなくなる」) の文言修正は対象外とする (h3 / http3 にも同一のコメントがあり、一貫した修正は別途判断する)。h3 / http3 / h2 / http2 の同名メソッドは変更しない (目的が QUIC クライアントの CONNECTION_CLOSE 送信検証のため)
- CONNECTION_CLOSE の生成と送出 (close() が生成し send() が返す) は実装済みであり、本 issue は戻り値の変更のみを行う。open issue 0031 (UDP ロス時の CONNECTION_CLOSE 再送) は低レベル側の変更であり重複しない。本 issue の検証は receive() を挟まないため 0031 実装後も成立する
- 検証テストは、バックグラウンド受信タスク (受信ループ内で `_send_pending()` を呼ぶ) が CONNECTION_CLOSE を先に消費して戻り値が 0 になる競合を避ける構成にする (例: 受信タスクを停止してその完了を待ってから `_connection.close()` → `_send_pending()` を検証する)

## 完了条件

- `_send_pending()` が送信パケット数を返す (送信しない場合は 0)
- `_connection.close()` を呼んだ後、受信タスクと干渉しない構成 (設計方針参照) で `_send_pending()` を呼び、戻り値が 1 になることと、続けて呼んだ 2 回目の戻り値が 0 になることを確認するテストを追加する
- 既存の全テストが通る

## 解決方法

(実装時に追記する)
