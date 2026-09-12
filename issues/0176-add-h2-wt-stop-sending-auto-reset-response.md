# WebTransport over HTTP/2 の WT_STOP_SENDING に対する WT_RESET_STREAM 自動応答と高レベルイベント配信を実装する

- Created: 2026-09-07
- Completed: 2026-09-12
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

## 解決方法

WT_STOP_SENDING への自動応答と高レベルイベント配信を実装した。

- `src/bindings/webtransport_h2.cpp` の `handle_wt_stop_sending` で、対象ストリームの情報を持つ場合 (`known_stream`) に `reset_stream(session_id, stream_id, error_code)` を呼び、送信側が Ready / Send 状態なら WT_RESET_STREAM を自動送出するようにした。エラーコードは受信した WT_STOP_SENDING から複製する (draft-15 Section 6.3 の can)。送信側が既に終端 (DataSent / ResetSent) の場合は `reset_stream` 側のガード (draft-15 Section 6.2 の MUST NOT) で送出されない
- `src/webtransport/h2/client.py` に `on_stop_sending(stream_id, error_code)`、`src/webtransport/h2/server.py` に `on_stop_sending(stream_id, error_code, session_writer)` を追加し、低レベルの `STOP_SENDING` イベントを配信するようにした
- 同じく両面に `stop_sending` 送信 API を追加した (`h2.Client.stop_sending` / `SessionWriter.stop_sending`)。既存の `reset_stream` と対称で、無いと自動応答を e2e で検証できないため
- テストを 4 本追加した
  - `test_stop_sending_on_ready_stream_auto_resets` (低レベル): サーバーが開いた Ready 状態のストリームへ WT_STOP_SENDING を送ると、エラーコードを複製し Reliable Size が送信済みバイト数と一致する WT_RESET_STREAM がワイヤに載る
  - `test_stop_sending_on_terminal_stream_does_not_auto_reset` (低レベル): 送信側が FIN 送出済みのストリームには自動応答しない (STOP_SENDING イベント自体は届く)
  - `test_server_on_stop_sending_fires` / `test_client_on_stop_sending_fires` (e2e): 高レベル両面のコールバックが stream_id とエラーコードを配信する
- `skills/webtransport-py/SKILL.md` の h2 のコールバック一覧と `SessionWriter` のメソッド一覧を更新した

`SESSION_DRAINING` への高レベル分岐は本 issue の対象外とした。低レベルでは `SessionDraining` イベントが既に配信されており (`tests/test_webtransport_h2_stop_sending_drain_session.py` で検証済み)、GOAWAY 系の通知は 0170 で `on_goaway` として実装済みのため重複を避けた。

`uv run pytest tests/ --timeout=30` の 1093 件が全て通る。
