# CONNECTION_CLOSE が UDP ロス時に再送されない

- Created: 2026-08-06
- Completed: YYYY-MM-DD
- Branch: feature/fix-connection-close-retransmission
- Polished: YYYY-MM-DD

## 目的

close() が生成した CONNECTION_CLOSE パケットが UDP ロスでピアに届かなかった場合、ピアは接続終了を検知できない問題を修正する。closing 状態のエンドポイントは受信パケットに応答して CONNECTION_CLOSE を再送するのが RFC 9000 の規定だが、現状は一度しか送出されない。

## 現状

- `src/bindings/quic.cpp` の `receive` メソッドは `closed_` が立った後の受信パケットをすべて破棄する (`if (!conn_ || closed_) { return 0; }`)
- `send` メソッドは `pending_close_packet_` を 1 回だけ返し、以降は `nullopt` を返す
- そのため、最初の CONNECTION_CLOSE が UDP ロスすると、ピアはアイドルタイムアウト (既定 30 秒) まで接続終了を検知できない
- RFC 9000 Section 10.2.1 は「An endpoint in the closing state sends a packet containing a CONNECTION_CLOSE frame in response to any incoming packet that it attributes to the connection」と規定する (受信パケットへの応答として CONNECTION_CLOSE を送る)。ただし「An endpoint SHOULD limit the rate at which it generates packets in the closing state」とレート制限は許容される

## 設計方針

- close() 後の `receive()` が closing 状態の接続に帰属するパケットを受信した場合、`pending_close_packet_` を再送する (1 回だけではなく、受信のたびに)
- レート制限 (RFC 9000 Section 10.2.1 の SHOULD limit the rate) を考慮し、無制限の再送は避ける (例: 受信パケットごとに 1 回の再送、または間隔制限)
- DRAINING 状態 (ピアの CONNECTION_CLOSE を受信済み) では再送しない
- 既存の `pending_close_packet_` の保持・消費の仕組みを拡張する (send() が返した後も、close() 後の receive() 応答用にパケットを保持しておく)

## 完了条件

- close() 後に送信した CONNECTION_CLOSE がピアに届かなかった場合、ピアからの再送パケットを受信すると CONNECTION_CLOSE が再送され、`send()` が返すこと
- モックなしのテストで確認する (close() → send() で CONNECTION_CLOSE を送出 → ピアがパケットを受信済みの状態で再度パケットを送ってくる → サーバーの receive() → send() が CONNECTION_CLOSE を再送することを検証)
