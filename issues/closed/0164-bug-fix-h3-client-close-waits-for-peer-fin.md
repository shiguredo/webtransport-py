# h3.Client.close() にピア FIN 待機がなく WT_CLOSE_SESSION の best-effort 配信を損なう

- Created: 2026-09-06
- Completed: 2026-09-09
- Branch: feature/fix-h3-client-close-waits-for-peer-fin
- Polished: 2026-09-07

## 目的

`h3.Client.close()` は `close_session` (WT_CLOSE_SESSION 送出) の直後に QUIC の `close()` (CONNECTION_CLOSE) を送出する。draft-ietf-webtrans-http3-16 Section 6 末尾の SHOULD (ピアの CONNECT ストリームクローズを待ってから CONNECTION_CLOSE を送り、WT_CLOSE_SESSION を best-effort で届ける) に従っていないため、WT_CLOSE_SESSION がピアに届く前に接続が閉じられ、アプリケーションの close 情報 (error code / message) が失われる可能性がある。SHOULD 不履行自体は仕様違反ではないが、best-effort 配信の改善として待機を実装する。

## 現状

- `src/webtransport/h3/client.py` の `Client.close` は `close_session` → `_send_pending` → `quic.close()` → `_send_pending` の順で実行
- ピアの FIN 待機ロジック無し
- `refs/webtrans/draft-ietf-webtrans-http3-16.txt` の Section 6 末尾「the endpoint SHOULD wait until all CONNECT streams have been closed by the peer before sending the CONNECTION_CLOSE; this gives WT_CLOSE_SESSION properties similar to that of the QUIC CONNECTION_CLOSE mechanism as a best-effort mechanism of delivering application close metadata」
- `src/bindings/webtransport_h3.h` の `H3Session::close_stream` doc は「高レベル Client では CONNECT ストリームの送信側が half-closed のままになり、ピアが完全クローズを待つ場合の相互運用に影響し得る (既知の制約)」と明記済み

## 設計方針

- `Client.close()` にピア FIN 待機フェーズを追加する。`close_session` 送出後、`close_wait_timeout` (コンストラクタ引数に新設。既定 3 秒。open issue 0122 項目 4 と同値に合わせて矛盾を解消する) の間、CONNECT ストリームのピア側 FIN 受信を待ってから `quic.close()` を送る
- 待機中は `close()` 内の独自ループで `_receive` と `_process_quic_events` と `_process_webtransport_events` と `_send_pending` を回す (`run()` とは別ループとし、`_running` 反転後のため二重 drain は起きない)。終了条件はピア FIN 受信またはタイムアウト到達のみとする
- タイムアウト時は `CONNECTION_CLOSE` を送出して close を完了する (SHOULD の best-effort 範囲内)
- `h3.Server.stop()` は対象外とする (Client と異なり `close_session` 自体を送っていないため別設計になる)
- GOAWAY 受信中 (draining 状態) の `close()` も待機規則は同一とし、0155 と相互前提を持たない (独立に実装できる)

## 対象・対象外

- 対象: `src/webtransport/h3/client.py` の `Client.close` (待機フェーズと `close_wait_timeout` 引数) / `tests/test_e2e_webtransport_h3.py` の待機テスト / `CHANGES.md`
- 対象外: `h3.Server.stop()` / h2 対称対応 (h2 draft-15 に相当 SHOULD がないため) / 低レベルバインディング (変更なし)

## 依存関係

- open issue 0122 項目 4 と同一対象 (h3 Client.close の FIN 待機) を扱う。本 issue が実装を担い、待機値・引数仕様を 0122 と整合させた。0122 項目 4 本文の更新 (本 issue への委譲明記) は 0122 側の作業として残る

## 完了条件

- ピアが FIN を返す通常経路で、`quic.close()` 送出前にピア FIN を受信すること (ワイヤ順序で WT_CLOSE_SESSION → FIN → CONNECTION_CLOSE になることを確認する)
- ピアが応答しない場合は `close_wait_timeout` (既定 3 秒) で close が完了すること
- `tests/test_e2e_webtransport_h3.py` に待機テスト (FIN 応答あり・なしの両変種) を追加すること
- 既存のテスト全 834 件が引き続き通過すること

## 解決方法

- `Client.close()` にピア終了待機を追加し、新規引数 `close_wait_timeout` (既定 3 秒) で上限を設ける。待機中は受信と送信とタイマーを回し、観測・中断・上限のいずれでも閉じる処理へ進む
- FIN と RESET の観測は待機開始前も含めて常時記録し、後始末は例外時も必ず行う
- `tests/test_e2e_webtransport_h3.py` に 5 件のテスト (終了観測・上限打ち切り・待機なし・リセット観測・冪等) を追加する
- 全 964 件のテストが通過することと、レビュー 5 周で致命的と重要が 0 件であることを確認した
