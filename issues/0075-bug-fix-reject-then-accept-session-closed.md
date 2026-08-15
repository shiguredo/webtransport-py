# reject_session で拒否した後に accept_session を呼ぶと SessionClosed が発火する

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-reject-then-accept-session-closed
- Polished: 2026-08-15

## 目的

サーバーが `reject_session` (非 2xx 応答) で拒否したセッションに、誤って `accept_session` を呼んだ場合に SessionClosed イベントが発火する問題を修正する。`reject_session` の意味論は「SessionClosed は発火しない (黙って削除)」であり、非 2xx で拒否されたセッションは一度も確立されていない (draft-ietf-webtrans-http3-16 Section 3.2 の「From the server's perspective, a session is established once it sends a 2xx response」の帰結) ため、誤用経路で発火する SessionClosed は終了通知の意味論に反する。誤用経路の API 挙動を「`accept_session` は false を返す (受理不可能の明示)」と明確にし、意味論との不整合を解消する。

## 現状

- 受理前にクライアントが送った WT_CLOSE_SESSION カプセルは nghttp3 の inq にバッファされ、サーバーが 2xx 応答を送出するまでは処理されない (draft-ietf-webtrans-http3-16 Section 3.2 の「A server MUST NOT process these bytes as capsules until it sends a 2xx response accepting the session」)
- この状態で `reject_session(0, 403)` を呼ぶと、`session_ids_` と `pending_pre_accept_fin_session_ids_` からセッション ID が削除され、SessionClosed は発火しない (黙って削除)。正しい使い方では問題ない
- その後、誤って `accept_session(0)` を呼ぶと (Sans-IO 構成で実測確認済み):
  - `nghttp3_conn_submit_wt_response` と `nghttp3_conn_server_confirm_wt_session` が成功し、`accept_session` は true を返す (誤用を検出できない)
  - confirm の処理中にバッファされた WT_CLOSE_SESSION カプセルが同期処理され、`recv_wt_close_session_cb` が発火して SessionClosed イベントが積まれる (受理前 WT_CLOSE_SESSION の破棄記録条件の拡大により、`accepting_session_id_` との一致で破棄記録も成立する)
  - `discard_stale_2xx` の close_stream により未送信の 403 と 2xx は両方破棄され、CONNECT ストリームには何も送出されない (reject 直後に `get_streams_to_send` で 403 を送出済みの場合は 403 のみが届く)
  - カプセルが未到着の構成 (受理前に WT_CLOSE_SESSION が届いていない) では破棄記録が成立せず、未送信の 200 が送出される (クライアントは非 2xx 受信済みのため確立は認識しない)
- `reject_session` の docstring (src/bindings/webtransport_h3.h の `reject_session`) は「SessionClosed イベントは発火しない (黙って削除)」と明記しているが、誤用経路ではこの意味論が崩れる
- 誤用経路であり、現実のアプリが踏む可能性は低いが、低レベル API の意味論としての不整合が残る (受理前 WT_CLOSE_SESSION の破棄記録条件の拡大の実装時に発見)

## 設計方針

誤用経路の扱いは (b) を採用する: `accept_session` が reject 済みセッション (非 2xx 拒否で `session_ids_` から削除済み) に対して false を返す (受理不可能の明示)。呼び出し側に誤用を検出させる。

- 実装は `accept_session` の既存ガード (conn_ / is_server_ / QPACK ストリーム未バインド) に続けて `session_ids_` のメンバーシップ確認を追加し、含まれない場合は false を返す。サーバー側の挿入は `end_headers_cb` が SESSION_READY の発火より前に行っているため、正常フロー (SESSION_READY 受信 → `accept_session`) のセッションは必ず含まれ、影響しない。reject 済みセッションの ID は削除済みのため false になる
- (b) では submit / confirm に進まないため、バッファされた WT_CLOSE_SESSION カプセルは処理されず `recv_wt_close_session_cb` も発火しない。SessionClosed は積まれない。`reject_session` が submit した非 2xx 応答はそのままクライアントに送出される (誤用時も拒否の通知が失われない)
- (a) を採らない理由: `recv_wt_close_session_cb` はイベント push の前に `session_ids_.erase` を実行するため判定位置 (erase の前後) に罠があり、共有コールバックの意味論変更になる。また `accept_session` が true を返すのにセッションが使えない (ID は削除済み) という非整合が残り、カプセル未到着構成では未送信の 200 が送出される (誤用を検出できない)
- (c) を採らない理由: 挙動を変えず文書化のみでは意味論の不整合が解消されず、bug カテゴリ・`feature/fix-` ブランチ・[FIX] エントリと整合しない
- 2xx 拒否後 (reject_session に 2xx を渡した場合) の `accept_session` 誤用は本 issue の対象外: 2xx 送出 = 確立のため ID が `session_ids_` に残り、(b) のガードが効かない (0072 が「h3 側の同種の誤用は別 issue で扱う」と宣言した経路)
- 変更対象は `src/bindings/webtransport_h3.cpp` (`accept_session` のガード追加)、`src/bindings/webtransport_h3.h` (`accept_session` / `reject_session` の docstring に誤用経路の挙動を追記)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- `reject_session` (非 2xx) で拒否した後に `accept_session` を呼ぶと false を返す
- 誤用経路で SessionClosed が発火しない
- 拒否時の非 2xx 応答がクライアントに送出される (誤用経路でも拒否の通知が失われない)
- 正常フロー (SESSION_READY 受信 → `accept_session`) は影響を受けない
- モックなしの Sans-IO テストで検証できる (conftest.py の `_create_session_pair` + `_setup_connect` で、受理前 WT_CLOSE_SESSION の注入 → `reject_session(0, 403)` → `accept_session(0)` の構成。false の返却・SessionClosed 不発火・非 2xx 応答の送出を確認する。カプセルの注入は `tests/test_webtransport_h3_pre_accept_fin.py` の既存ヘルパー相当で行う。テストは `tests/test_webtransport_h3_server_reject_session.py` への追加が自然)
- `reject_session` / `accept_session` のコメントと docstring が実挙動と整合する
