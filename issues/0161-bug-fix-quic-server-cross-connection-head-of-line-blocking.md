# quic.Server の run() が単一ループのため 1 クライアントのコールバック実行中に他クライアントの受信・ACK・タイマー処理が完全に止まる

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-quic-server-cross-connection-head-of-line-blocking
- Polished: {YYYY-MM-DD}

## 目的

`quic.Server.run()` は 1 datagram を受信 → コールバックを await → 送信、を単一ループで直列に処理する。あるクライアントのコールバックが長時間 await すると、他クライアントの受信・ACK・タイマー処理 (`handle_timeout` を含む) が完全に停止する。実験ではクライアント A の `on_stream_data` を 0.5 秒待たせるだけでクライアント B のエコー往復が約 500 ms 遅延した。コールバック実行時間がピアの PTO を超えると無関係な接続に spurious retransmit を強い、`idle_timeout_ns` を超えるとそれらが idle timeout で切断される可用性のバグ。

## 現状

- `src/webtransport/quic/server.py` の `Server.run` は `while self._running:` 本体で 1 datagram を `sock_recvfrom` して 1 コールバック実行までを await し切る
- `Server._send_to(addr, connection)` は受信したクライアント宛にのみ送信 (他クライアントの ACK / パケットは同時には出ない)
- 全接続のタイマーを回す `for addr, connection in list(self._connections.items())` ループも同じ本体の末尾にあるため、任意のコールバック実行中はどの接続も read / ACK / handle_timeout を受けられない
- 実験 (2 クライアント同時接続、A の `on_stream_data` で `await asyncio.sleep(0.5)`) のサーバー側ログ: `recv A +1.0 ms / echo A +501.8 ms / recv B +502.9 ms / echo B +503.0 ms` (B 単独の往復は 0.4 ms)。B の pkt_sent が膨張し spurious retransmit を確認
- 同型の構造は `h3.Server` / `http3.Server` にも存在する可能性が高い (それらは別 issue で対応)

## 設計方針

- 受信ループとコールバック実行を分離する。受信ループはパケット受信・イベント drain・送信のみを行い、コールバックは別タスク (`asyncio.create_task` またはクライアントごとの単独タスク) に切り出す
- 送信を「受信した接続だけでなく全接続に対して行う」に変える (`_send_all` のような形)。あるクライアントの受信を契機に他クライアントの ACK が出せるようにする
- コールバック実行中に接続の受信・タイマーが止まらないよう、ロックまたはキュー経由でイベントを供給する
- コールバックがサーバーの内部状態 (`_connections` 等) を触る場合の同期を設計 (現状の asyncio 単一スレッド前提を維持するなら明示的な逐次化で十分)

## 完了条件

- クライアント A の `on_stream_data` が 0.5 秒 await している間に、クライアント B のエコー往復が 50 ms 以内で完了すること
- 100 接続が並行して転送する状況で、単一クライアントの遅延が他クライアントに 100 ms 以上波及しないこと
- `tests/` に複数クライアント同時接続時の分離性を検証するテストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
