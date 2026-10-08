# h3 の client reset e2e テストが終端済みストリームへ reset して CI で失敗する

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/test-fix-h3-client-reset-e2e-flake
- Reporter: @voluntas

## 目的

`tests/test_e2e_http3.py` の `test_client_resets_http3_stream` が断続的に失敗して
develop の CI を赤くするのを解消する。2026-10-08 の wheel ワークフローでは
ubuntu-26.04 x86_64 (3.14) と macos-26_arm64 (3.14t) の 2 ジョブが同じこのテストで
落ち、残りの 1346 テストは通過した。

## 現状

- 失敗は `await asyncio.wait_for(server_reset_received.wait(), timeout=5.0)` の
  TimeoutError であり、サーバーの `on_stream_reset` が呼ばれない
- ローカル (macOS) で 10 回中 1 回再現する。`Client.reset_stream` の呼び出し前に
  1 ms 以上の遅延を挟むと 15 回中 15 回再現する
- 原因は reset 対象にするストリームの作り方にある。高レベル `http3.Client.request` は
  リクエストを終端する (FIN を送出する) ため、テストが
  `client.request("GET", "/will-reset")` の戻り値をそのまま reset すると、クライアントの
  run ループがサーバーの ACK を先に処理した場合に RESET_STREAM がワイヤへ出ない
- ngtcp2 の `ngtcp2_conn_shutdown_stream_write` は、送信データと FIN がすべて
  ACK 済み (`ngtcp2_strm_is_all_tx_data_fin_acked`) のときは何もせず戻る。
  RFC 9000 Section 3.1 の送信側の状態遷移では、FIN が ACK された Data Recvd 状態から
  RESET_STREAM を送出できないため、この挙動は仕様どおりである
- 同じ契約変更に `tests/test_e2e_http3_peer_stop_sending.py` の
  `test_client_forwards_peer_stop_sending` は追随済みで、終端しないストリームを
  低レベル API で用意する形へ移行している。`test_client_resets_http3_stream` だけが
  旧契約のまま残っている
- 影響: テストが実行タイミング依存になり、CI が確率的に失敗する

## 設計方針

- ライブラリ側は変更しない。RESET_STREAM を送出できない状態での no-op は
  RFC 9000 Section 3.1 に沿った挙動であり、`Client.reset_stream` の契約として正しい
- テストを「送信側が開いたままのストリームを reset する」形へ移行する。
  1 本目は公開 API (`Client.request`) で送って HTTP/3 の制御・QPACK ストリームの設定を
  済ませ、2 本目は低レベル API (`Client._quic_connection` の `open_stream` と
  `Client._http3_connection` の `submit_request`) で終端せずに開き、その stream_id を
  `Client.reset_stream` で reset してサーバーの `on_stream_reset` を検証する
- 変更対象: `tests/test_e2e_http3.py` の `test_client_resets_http3_stream`

## 完了条件

- `test_client_resets_http3_stream` が reset の到着をタイミングに依存せず検証する
  (30 回連続実行で失敗しない)
- 全テストが通過する
- CI が緑に戻る

## 解決方法
