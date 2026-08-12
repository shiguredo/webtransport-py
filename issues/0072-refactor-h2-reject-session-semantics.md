# HTTP/2 の reject_session が 2xx 応答でもセッション ID を削除する

- Created: 2026-08-12
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-h2-reject-session-semantics
- Polished: {YYYY-MM-DD}

## 目的

HTTP/2 側の `reject_session` が status_code に関わらず `wt_sessions_` からセッション ID を削除するのに対し、HTTP/3 側の `reject_session` は「2xx 以外のときのみ削除 (2xx 送出 = 確立)」に統一された。同一ライブラリの対称 API で意味論が分かれており、将来 h2 側でも 2xx 応答の生成に `reject_session` を使ったときに確立済みセッションが黙って消えるバグの温床になるため、意味論を揃える。

## 現状

- `src/bindings/webtransport_h2.cpp` の `reject_session` は `nghttp2_submit_response` を呼んだ後に `wt_sessions_.erase(session_id)` を status_code に関わらず実行する (submit の戻り値も確認しない)
- `src/bindings/webtransport_h3.cpp` の `reject_session` は `status_code / 100 != 2` のときのみ `session_ids_` と `pending_pre_accept_fin_session_ids_` から削除する (draft-ietf-webtrans-http3-16 Section 3.2 の「2xx 応答の送出時点でセッションが確立される」に基づく)
- h2 側のテスト (`tests/prop_webtransport_h2.py` の `prop_reject_session_arbitrary`) は任意の status_code を渡してクラッシュしないことのみ確認しており、2xx を渡すユースケースは存在しない (h3 側は 201 応答の生成に `reject_session(0, 201)` を使うテストがある)
- h2 側の根拠は draft-ietf-webtrans-http2-15 にあり、2xx 送出 = 確立の意味論は http3 と共通 (Section 3.2)

## 設計方針

- h3 と対称に「2xx 以外のときのみ `wt_sessions_` から削除」に揃えることを基本とする (2xx 送出 = 確立。draft-ietf-webtrans-http2-15 の対応節を一次資料で確認してから確定する)
- h2 側の確立判定 (`is_established` の設定箇所) と `reject_session` の削除条件が整合することを確認する
- 挙動を変える場合は、`prop_reject_session_arbitrary` の拡張か、2xx (例: 201) 拒否時にセッション ID が残ることを確認するテストを追加する

## 完了条件

- `reject_session` の 2xx 応答時の挙動が h3 と揃っている (または、差異を維持する根拠がドキュメント化されている)
- 2xx (例: 201) を渡した場合に `wt_sessions_` のエントリが残り、非 2xx (例: 403) で削除されることをテストで確認できる
- 全テストが通る
