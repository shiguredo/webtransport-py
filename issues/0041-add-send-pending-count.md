# 高レベル QUIC クライアントの _send_pending と close が送信パケット数を返すようにする

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-send-pending-count
- Polished: 2026-08-09
- Reporter: @voluntas

## 目的

`_send_pending` が送信したパケット数を返すようにし、CONNECTION_CLOSE の送信検証を可能にする。あわせて、公開 API の `Client.close()` からも送信したパケット数を取得できるようにし、`_connection` や `_send_pending()` などの private 属性・メソッドに触れずに CONNECTION_CLOSE の実送出を確認できるようにする。

## 現状

- webtransport-py の高レベル `Client._send_pending()` (src/webtransport/quic/client.py) は送信待ちパケットを 1 つ送出して `None` を返す
- webtransport-py の高レベル `Client.close()` (src/webtransport/quic/client.py) は内部で `_connection.close()` → `_send_pending()` を順に呼ぶが戻り値は `None` であり、CONNECTION_CLOSE が実際に送出されたか (ソケット送信が完了したか) を確認する公開 API が存在しない
- sora-quic の `test_ngtcp2_close_sends_connection_close_packet` は `client._connection.close()` を呼んだ後に `sent_packets = await client._send_pending()` の戻り値が 0 より大きいことを検証する
- ngtcp2-py の `_send_pending(max_packets=None)` は送信したパケット数を返す

## 設計方針

- `_send_pending()` が送信したパケット数を返すようにする (パケットを送信しない場合は 0)。1 呼び出しあたり 1 パケットの制約は変更しないため、戻り値は 0 か 1 になる
- `Client.close()` の戻り値も `int` に変更し、close() 内部で呼ぶ `_send_pending()` の戻り値 (CONNECTION_CLOSE 送出成否に対応する 0 か 1) をそのまま返す。close() が複数回 `_send_pending()` を呼ぶ実装に将来変更された場合は合計値を返す (現状は 1 回のため 0 か 1)。socket クローズ経路 (`_send_pending()` が OSError を送出した場合や `_connection` が None の場合) では 0 を返す
- 変更対象は src/webtransport/quic/client.py の高レベル `Client._send_pending()` と `Client.close()` (シグネチャの変更と docstring の Returns 追記を含む) と tests/test_e2e_quic.py へのテスト追加のみ。既存の 1 パケット制約の理由づけ (「ACK なしで連続 drain すると戻ってこなくなる」) の文言修正は対象外とする (h3 / http3 にも同一のコメントがあり、一貫した修正は別途判断する)。h3 / http3 / h2 / http2 の同名メソッドは変更しない (目的が QUIC クライアントの CONNECTION_CLOSE 送信検証のため)
- CONNECTION_CLOSE の生成と送出 (close() が生成し send() が返す) は実装済みであり、本 issue は戻り値の変更のみを行う。open issue 0031 (UDP ロス時の CONNECTION_CLOSE 再送) は低レベル側の変更であり重複しない。本 issue の検証は receive() を挟まないため 0031 実装後も成立する
- 検証テストは、バックグラウンド受信タスク (受信ループ内で `_send_pending()` を呼ぶ) が CONNECTION_CLOSE を先に消費して戻り値が 0 になる競合を避ける構成にする (例: 受信タスクを停止してその完了を待ってから `_connection.close()` → `_send_pending()` を検証する)。`Client.close()` の戻り値を検証するテストでは、close() 内でバックグラウンド受信タスクが停止・回収されてから `_send_pending()` が呼ばれる (src/webtransport/quic/client.py の `close()` の実装順) ため、受信タスクとの競合は起きない
- `Client.close()` の呼び出し元 (アプリケーション) の互換性: 既存コードは戻り値を無視して呼び出しているため、`None` から `int` への変更で `await client.close()` は動作を継続する

## 完了条件

- `_send_pending()` が送信パケット数を返す (送信しない場合は 0)
- `Client.close()` が close() 中に送信したパケット数を返す (CONNECTION_CLOSE が送出できた場合は 1、`_connection` が未初期化などで送出しない場合は 0)
- `_connection.close()` を呼んだ後、受信タスクと干渉しない構成 (設計方針参照) で `_send_pending()` を呼び、戻り値が 1 になることと、続けて呼んだ 2 回目の戻り値が 0 になることを確認するテストを追加する
- 接続確立済みの `Client` に対して `await client.close()` を呼び、戻り値が 1 になることを確認するテストを追加する (公開 API のみで CONNECTION_CLOSE 送出を観測できることの検証)
- 既存の全テストが通る

## 解決方法

(実装時に追記する)
