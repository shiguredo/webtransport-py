# h3.Client.close() が draft-ietf-webtrans-http3-16 Section 6 の「全 CONNECT ストリームがピアに閉じられるまで CONNECTION_CLOSE を待つ SHOULD」に説明なく違反する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-client-close-waits-for-peer-fin
- Polished: {YYYY-MM-DD}

## 目的

`h3.Client.close()` は `close_session` (WT_CLOSE_SESSION + FIN 送出) の直後に QUIC の `close()` (CONNECTION_CLOSE) を送出する。draft-ietf-webtrans-http3-16 Section 6 末尾は「the endpoint SHOULD wait until all CONNECT streams have been closed by the peer before sending the CONNECTION_CLOSE; this gives WT_CLOSE_SESSION properties similar to that of the QUIC CONNECTION_CLOSE mechanism as a best-effort mechanism of delivering application close metadata」を要求する。現状はピアの FIN 待機なしで CONNECTION_CLOSE を送るため、WT_CLOSE_SESSION がピアに届く前に接続が閉じられ、アプリケーションの close 情報 (error code / message) が失われる可能性がある。

## 現状

- `src/webtransport/h3/client.py` の `Client.close` は `close_session` → `_send_pending` → `quic.close()` → `_send_pending` の順で実行
- ピアの FIN 待機ロジック無し
- `refs/webtrans/draft-ietf-webtrans-http3-16.txt` L1554-1557 「the endpoint SHOULD wait until all CONNECT streams have been closed by the peer before sending the CONNECTION_CLOSE; this gives WT_CLOSE_SESSION properties similar to that of the QUIC CONNECTION_CLOSE mechanism as a best-effort mechanism」
- `src/bindings/webtransport_h3.h` の `H3Session::close_stream` doc は「高レベル Client では CONNECT ストリームの送信側が half-closed のままになり、ピアが完全クローズを待つ場合の相互運用に影響し得る (既知の制約)」と明記済み

## 設計方針

- `Client.close()` に「ピアの FIN 待機フェーズ」を追加する。`close_session` 送出後、CONNECT ストリームのピア側 FIN 受信または一定時間 (例: idle_timeout の 1/2 か 500 ms) の経過を待ってから `quic.close()` を送る
- 待機中はイベント drain を継続し、ピアから受信した WT_CLOSE_SESSION に対する応答 FIN 等を処理する
- タイムアウト時は仕様の SHOULD であり必須ではないため CONNECTION_CLOSE を送出して close を完了する
- `h3.Server.stop()` にも同様の待機を検討する (別 issue 予定)
- issue 0155 (GOAWAY 受信で closed_) と整合させる (draining 状態の管理と共存)

## 完了条件

- ピアが FIN を返す通常経路で、WT_CLOSE_SESSION がピアに配信されてから CONNECTION_CLOSE が送出されること
- ピアが応答しない場合はタイムアウトで close が完了すること
- `tests/` に「WT_CLOSE_SESSION の受信を待ってから CONNECTION_CLOSE を送る」を検証する Sans-IO テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
