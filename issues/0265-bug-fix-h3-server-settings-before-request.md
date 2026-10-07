# h3 サーバーがクライアント SETTINGS 受信前に WebTransport リクエストを処理する

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-server-settings-before-request

## 目的

draft-ietf-webtrans-http3-16 Section 3.1 と Section 7.1 が WebTransport の draft 版に限って定める「サーバーはクライアントの SETTINGS を受信するまで WebTransport リクエストを処理してはならない (MUST NOT)」を満たす。

## 現状

- draft-ietf-webtrans-http3-16 Section 3.1 は「For draft versions of WebTransport only, the server MUST NOT process any incoming WebTransport requests until the client's SETTINGS have been received; see Section 7.1.」と定める。Section 7.1 も「the server MUST NOT process any incoming WebTransport requests until the client's SETTINGS have been received.」と同旨を定める
- `src/bindings/webtransport_h3.cpp` の `H3Session::settings_received_` は `H3Session::recv_settings2_cb` で立つが、参照しているのは `H3Session::is_webtransport_ready` のみである。`is_webtransport_ready` は「サーバーセッションでは nghttp3 が対向の ENABLE_CONNECT_PROTOCOL を記録しないため常に偽になる」と docstring にあり、クライアント用である
- `H3Session::end_headers_cb` は `:method` / `:protocol` の判定と Origin 検証だけを行い、`session_ids_` へ挿入して `H3EventType::SessionReady` を push する。`settings_received_` を見ていないため、クライアントの SETTINGS 到着前でも WebTransport リクエストを処理する
- SETTINGS はクライアントの制御ストリーム、CONNECT は要求ストリームで別々に届くため、到着順は保証されない。draft が本 MUST を置いたのはこのためである
- 影響: 版数ネゴシエーションの前提 (サーバーがクライアントの版を知ってからストリームヘッダーの wire format を解釈する) が崩れる。本実装は 1 版のみ扱うため現時点の実害は小さいが、MUST 未達である

## 設計方針

- サーバー側 `H3Session::end_headers_cb` で `settings_received_` を確認する。未受信なら `SessionReady` を発火せず、`session_ids_` にも登録しない。SETTINGS 受信後に処理を再開できるよう、保留したストリームを保持する
- 保留の扱いは既存の受理前ストリームの扱い (`pending_headers_`、受理前 FIN / リセットの検出、受理前バッファ上限) と揃える。保留中に FIN や RST_STREAM が届いた場合の後始末を既存経路と共有する
- SETTINGS 受信時に保留分を到着順で処理し、既存の確立処理 (Origin 検証、403 応答、受理、`SessionReady` の発火) と同じ結果になるようにする
- 却下案: SETTINGS 未受信の WebTransport リクエストを 4xx で拒否する。draft は「処理するな」であり「拒否せよ」ではない
- 変更対象: `src/bindings/webtransport_h3.cpp` / `.h`、`tests/test_webtransport_h3_settings_ready.py` (および必要なら専用テストファイル)

## 完了条件

- クライアントの SETTINGS より先に WebTransport CONNECT が到着した場合、SETTINGS 受信まで `SessionReady` が発火せず、`session_ids_` にも登録されない
- SETTINGS 受信後に同じストリームが処理され、通常の確立と同じ結果になる (受理、Origin 検証による 403、拒否が既存挙動どおり)
- 保留中に FIN や RST_STREAM が届いても、既存の受理前ストリームの後始末と矛盾しない (ストリームやセッションがリークしない)
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する

## 解決方法
