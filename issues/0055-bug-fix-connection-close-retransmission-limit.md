# CONNECTION_CLOSE の再送が CLOSING 期間満了後も無期限に続く

- Created: 2026-08-09
- Completed: YYYY-MM-DD
- Branch: feature/fix-connection-close-retransmission-limit
- Polished: {YYYY-MM-DD}

## 目的

close() 後の受信パケットへの応答として CONNECTION_CLOSE を再送する実装 (closed issue 0031) で、再送が CLOSING 期間 (RFC 9000 Section 10.2 の「at least three times the current PTO interval」) 満了後も無期限に続く問題を修正する。

## 現状

- `src/bindings/quic.cpp` の `receive` メソッドは、close() で CONNECTION_CLOSE を生成できた接続に対して closed_ 後も受信パケットを処理し、`NGTCP2_ERR_CLOSING` を受けるたびに `close_packet_armed_` を true に戻して再送する
- `src/bindings/quic.cpp` の `get_timeout_ns` / `handle_timeout` は `closed_` ガードで早期 return するため、CLOSING 期間 (約 3×PTO) の満了がアプリ層で処理されない
- その結果、ピアが送信を続ける限り、同一 CONNECTION_CLOSE を無期限に再送し続ける。1:1 応答で増幅にはならないが、接続状態が破棄されず資源を消費し続ける
- RFC 9000 Section 10.2 は closing / draining 状態を「SHOULD persist for at least three times the current PTO interval」とし、その後に「MAY send a Stateless Reset in response to any further incoming packets」と定める。無制限の応答はこの設計意図に反する

## 設計方針

- close() 直後に `get_timeout_ns` が CLOSING 期間の満了時刻を返すようにし、`handle_timeout` で満了時に再送を停止する (対応の一例。具体的な停止条件・破棄タイミングは実装時に判断)
- 再送の停止後は `send()` が CONNECTION_CLOSE を返さないこと、受信パケットに応答しないことを確認する
- 高レベル API は close() 後に受信ループを回さないため、本修正の効果は低レベル API (Sans-IO) に限られる (closed issue 0031 と同じ線引き)

## 完了条件

- CLOSING 期間満了後、ピアがパケットを送り続けても CONNECTION_CLOSE が再送されない
- CLOSING 期間内は従来どおり受信パケットごとに 1 回再送される
- モックなしの Sans-IO テストで確認する
