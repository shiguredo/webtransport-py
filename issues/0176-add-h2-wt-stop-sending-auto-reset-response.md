# WebTransport over HTTP/2 の WT_STOP_SENDING に対する WT_RESET_STREAM 自動応答と高レベルイベント配信を実装する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h2-wt-stop-sending-auto-reset-response
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 6.3 は「the recipient of a WT_STOP_SENDING capsule sends a WT_RESET_STREAM capsule in response if the stream is in the "Ready" or "Send" state」(RFC 9000 Section 3.5 由来の MUST) を求める。`H2Session::handle_wt_stop_sending` はイベントを積むだけで自動応答しない。加えて高レベル `h2.Client` / `h2.Server` は STOP_SENDING / SESSION_DRAINING イベントに対する分岐を持たず、アプリに配信されない。仕様の MUST 未実装。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_stop_sending` は `StopSending` イベントを push するのみ
- 対照: `H2Session::report_stream_state_error` は WT_STOP_SENDING の二重受信を検知するが、初回の応答は無い
- `src/webtransport/h2/client.py` の `Client.run` と `src/webtransport/h2/server.py` の `Server._handle_client` は `STOP_SENDING` / `SESSION_DRAINING` の分岐を持たない (grep で 0 件)
- draft-15 Section 6.3 / RFC 9000 Section 3.5 (refs 外)
- draft-15 Section 5.2 の QUIC 状態ミラー: 送信側が Ready / Send 状態なら受信 WT_STOP_SENDING に WT_RESET_STREAM で応答する

## 設計方針

- `H2Session::handle_wt_stop_sending` で、対象ストリームの `send_state` が `Ready` の場合、自動的に WT_RESET_STREAM を送出する (`reset_stream` を呼ぶ)。エラーコードは受信した WT_STOP_SENDING の error_code をコピーする (draft-15 Section 6.3 の「The error code from the WT_STOP_SENDING capsule can be copied into the WT_RESET_STREAM capsule if the endpoint does not have a more appropriate code to use」に従う)
- 高レベル `h2.Client.on_stop_sending` / `h2.Server.on_stop_sending` (仮) コールバックを追加し、STOP_SENDING イベントをアプリに配信する
- 同様に `h2.Client.on_session_draining` / `h2.Server.on_session_draining` (仮) を追加する (issue 0170 と統合検討)
- 自動応答をアプリがオーバーライドできるよう、コールバックの戻り値で「自動応答するかどうか」を制御する API も検討する
- `WtStreamInfo::send_state` の状態遷移が draft-15 Section 5.2 (RFC 9000 Section 3.5 ミラー) と整合していることを確認する

## 完了条件

- WT_STOP_SENDING 受信で対象ストリームが Ready / Send 状態なら WT_RESET_STREAM が自動送出されること
- 高レベル `on_stop_sending` (仮) コールバックでアプリが STOP_SENDING を検知できること
- `tests/` に WT_STOP_SENDING の自動応答テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
