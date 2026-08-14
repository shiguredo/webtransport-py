# reject_session で拒否した後に accept_session を呼ぶと SessionClosed が発火する

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-reject-then-accept-session-closed
- Polished: {YYYY-MM-DD}

## 目的

サーバーが `reject_session` (非 2xx 応答) で拒否したセッションに、誤って `accept_session` を呼んだ場合に SessionClosed イベントが発火する問題を修正する。`reject_session` の意味論は「SessionClosed は発火しない (黙って削除)」であり (draft-ietf-webtrans-http3-16 Section 3.2 の「非 2xx で拒否されたセッションは一度も確立されていない」)、誤用時にこの意味論が崩れる。誤用経路の API 挙動を明確にし、意味論との不整合を解消する。

## 現状

- 受理前にクライアントが送った WT_CLOSE_SESSION カプセルは nghttp3 の inq にバッファされる (draft-ietf-webtrans-http3-16 Section 3.2 の「A server MUST NOT process these bytes as capsules until it sends a 2xx response」)
- この状態で `reject_session(0, 403)` を呼ぶと、`session_ids_` と `pending_pre_accept_fin_session_ids_` からセッション ID が削除され、SessionClosed は発火しない (黙って削除)。正しい使い方では問題ない
- その後、誤って `accept_session(0)` を呼ぶと (Sans-IO 構成で実測確認済み):
  - `nghttp3_conn_submit_wt_response` と `nghttp3_conn_server_confirm_wt_session` が成功し、`accept_session` は true を返す
  - confirm の処理中にバッファされた WT_CLOSE_SESSION カプセルが同期処理され、`recv_wt_close_session_cb` が発火して SessionClosed イベントが積まれる (受理前 WT_CLOSE_SESSION の破棄記録条件の拡大により、`accepting_session_id_` との一致で破棄記録も成立する)
  - `discard_stale_2xx` の close_stream により、未送信の 403 と 2xx は両方破棄されクライアントには何も送られない
- `reject_session` の docstring (src/bindings/webtransport_h3.h の `reject_session`) は「SessionClosed イベントは発火しない (黙って削除)」と明記しており、実挙動と矛盾する
- 誤用経路であり、現実のアプリが踏む可能性は低いが、低レベル API の意味論としての不整合が残る (受理前 WT_CLOSE_SESSION の破棄記録条件の拡大の実装時に発見)

## 設計方針

誤用経路の扱いを次のいずれかで決める (実装時に判断し、`reject_session` / `accept_session` / `recv_wt_close_session_cb` のコメントを整合させる):

- (a) `recv_wt_close_session_cb` の発火時にセッション ID が `session_ids_` に含まれない (reject 済み等で削除済み) 場合は SessionClosed を積まない。拒否済みセッションの終了通知という意味論が合わないため、黙って削除の意味論に揃える
- (b) `accept_session` が reject 済みセッションに対して false を返す (受理不可能の明示)。呼び出し側に誤用を検出させる
- (c) 文書化のみ (誤用時は未定義としてコメントに明記)

## 完了条件

- 受理前に WT_CLOSE_SESSION がバッファされたセッションを `reject_session` (非 2xx) で拒否した後に `accept_session` を呼んでも、SessionClosed が発火しない (設計方針 (a) の場合)。または `accept_session` が false を返す (設計方針 (b) の場合)
- モックなしの Sans-IO テストで検証できる (conftest.py の `_create_session_pair` + `_setup_connect` で、カプセル注入 → `reject_session(0, 403)` → `accept_session(0)` の構成)
- `reject_session` / `accept_session` / `recv_wt_close_session_cb` のコメントと docstring が実挙動と整合する
