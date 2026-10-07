# h2 の高レベル層にセッション終了理由が伝わらない

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h2-session-close-reason

## 目的

WebTransport over HTTP/2 で、セッション終了の理由 (ピアの WT_CLOSE_SESSION の Application Error Code とメッセージ、ローカル検知のエラーコード) を高レベル API から観測できるようにする。現状は高レベルで正常終了と異常終了を区別できず、E2E テスト向けライブラリとして異常終了の原因を追えない。

## 現状

- draft-ietf-webtrans-http2-15 Section 3.4 は「Errors can be reported using the WT_CLOSE_SESSION capsule, which includes an error code and an optional explanatory message.」と定める
- 低レベルは理由を持つ。`src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_close_session` は `H2EventType::SessionClosed` に Application Error Code とメッセージを載せる (`event.error_message` を組み立てている箇所)
- 一方、ローカル検知の異常終了は `H2Session::report_stream_state_error` が `H2EventType::Error` (0x51) を push して `H2Session::close_session` を呼び、その後に届く `H2Session::on_stream_close_callback` が nghttp2 のクローズコード (通常 0) を載せた `H2EventType::SessionClosed` を push する
- 高レベル `h2.Client.on_error` / `h2.Server.on_error` は `WT_FLOW_CONTROL_ERROR` のみを渡す (0237 の記録どおりの設計)。`on_session_closed` の引数はクライアントが `session_id`、サーバーが `SessionWriter` のみで、終了理由が渡らない
- 影響: 0x51 で閉じたセッションもピアの WT_CLOSE_SESSION による終了も高レベルでは「正常終了」として観測される。0x51 で閉じたセッションが `error_code` 0 の SessionClosed として通知される点は 0237 の記録と食い違う
- h3 側も同型の API 設計である (h3 の API 追加は 0257 / 0198 が扱う)

## 設計方針

- 高レベル API に終了理由を渡す形を決める。候補は (a) `on_session_closed` に `error_code` / `error_message` を追加、(b) 終了理由専用のコールバックを追加、(c) `on_error` の転送範囲を広げる。h3 側 (0257 / 0198) と対称になる形を優先する
- ピアの WT_CLOSE_SESSION 由来か、ローカル検知 (0x51 等) か、HTTP/2 の接続エラーかを区別できるようにする。区別できないとアプリが原因を誤認する
- 0237 が「WT_STREAM_STATE_ERROR は高レベル `on_error` に渡さない」と決めているため、その決定を変える場合は理由を issue に残す
- 本 issue は h2 層の変更に限定する。h3 層の同種の対応は 0257 / 0198 に委ねる
- 変更対象: `src/webtransport/h2/client.py` / `server.py`、`skills/webtransport-py/SKILL.md`、`tests/`

## 完了条件

- ローカル検知の異常終了 (0x51) と正常終了が高レベル API で区別できる
- ピアの WT_CLOSE_SESSION の Application Error Code とメッセージを高レベル API で観測できる
- 終了理由を渡さない既存の使い方 (引数の互換) を壊す場合は `skills/webtransport-py/SKILL.md` とテストを合わせて更新する
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する

## 解決方法
