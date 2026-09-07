# quic.Server の run() が単一ループのため 1 クライアントのコールバック実行中に他クライアントの受信・ ACK ・タイマー処理が完全に止まる

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-quic-server-cross-connection-head-of-line-blocking
- Polished: 2026-09-07

## 目的

`quic.Server.run()` は 1 datagram を受信 → 複数イベントの drain と複数コールバックの await し切り → 送信、を単一ループで直列に処理する。あるクライアントのコールバックが長時間 await すると、他クライアントの受信・ ACK ・タイマー処理 (`handle_timeout` を含む) が完全に停止する。実験ではクライアント A の `on_stream_data` を 0.5 秒待たせるだけでクライアント B のエコー往復が数百 ms オーダー遅延した。コールバック実行時間がピアの PTO を超えると無関係な接続に spurious retransmit を強い、`idle_timeout_ns` を超えるとそれらが idle timeout で切断される可用性のバグ。

## 現状

- `src/webtransport/quic/server.py` の `Server.run` は `while self._running:` 本体で 1 datagram を `sock_recvfrom` し、drain した複数イベントのコールバック実行までを await し切る
- `Server._send_to(addr, connection)` は受信したクライアント宛にのみ送信 (他クライアントの ACK / パケットは同時には出ない)
- 全接続のタイマーを回す `for addr, connection in list(self._connections.items())` ループも同じ本体の末尾にあるため、任意のコールバック実行中はどの接続も read / ACK / handle_timeout を受けられない
- 実験 (Sans-IO ではなく高レベル Server + 実ソケットの 2 クライアント。検証済み): A の `on_stream_data` で当該 addr の場合のみ `await asyncio.sleep(0.5)` し、B の送信→受信の wall time を測ると約 450 ms (環境依存の参考値。受信・送信・タイマー・PTO の実時間がすべて止まる方向性はコードと一致する)。`pkt_sent` 膨張の厳密な計測手順は未整備のため、spurious retransmit と idle timeout 切断は機構上の帰結として扱う
- 同型の構造は `h3.Server` / `http3.Server` にも存在する (コードで確認済み。それらは別 issue で対応し、本 issue の対象外とする)

## 設計方針

- 受信ループは `sock_recvfrom` による受信・`receive`・`next_event` の drain・送信・タイマーのみを行い、アプリコールバックを await しない。drain したイベントは接続ごとの `asyncio.Queue` に投入し、接続ごとの単独タスクが取り出してコールバックを実行する。イベントごとの `create_task` は選ばない (同一接続内のイベント順序保証とタスク寿命管理のため)。タスク寿命は接続存続期間とし、新規 addr 時に生成、CONNECTION_CLOSED 検知時と `stop` 時に破棄する。タスク内例外はログ出力後に当該接続を閉じる
- 送信は毎反復で全接続を走査する (既存の末尾タイマーループを拡張し、受信の有無にかかわらず全接続の `send` と `get_timeout` / `handle_timeout` を処理する)。`OSError` は接続単位で隔離する (`stop` の既存方針に合わせ、`run` 本体を落とさない)
- 同期は単一スレッド asyncio のままロックなしとする。`_connections` の削除は受信ループ側のみが行い、クライアントタスクは保持した Connection 参照を使う。削除済み接続への送信は no-op ガードする。`send_stream_data` 等の外部 API とクライアントタスクの割り込み関係は await 境界で整理し、要否を実装時に確定する

## 完了条件

- クライアント A の `on_stream_data` が 0.5 秒 await している間に、クライアント B の送信→受信の wall time が 50 ms 以内で完了すること (50 ms は CI 環境変動を吸収する上限であり、厳密な性能保証ではない)
- 100 接続が並行して転送する状況で、0.5 秒 sleep する 1 クライアント以外の全接続の往復がいずれも 100 ms 以内であること (波及の定義は各接続の往復時間の最大値とする)
- `tests/test_e2e_quic_isolation.py` を新規作成し、実ソケット 2 クライアントの分離テストと 100 接続負荷テストを追加すること (Sans-IO では実時間の分離を検証できないため e2e 形式とする)
- 既存のテスト全 822 件が引き続き通過すること
