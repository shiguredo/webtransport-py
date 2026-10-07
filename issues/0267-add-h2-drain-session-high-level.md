# h2 の高レベル層に WT_DRAIN_SESSION の送信と受信通知が無い

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h2-drain-session-high-level

## 目的

WebTransport over HTTP/2 で、draft-ietf-webtrans-http2-15 Section 6.13 の WT_DRAIN_SESSION capsule を高レベル API から送受信できるようにする。低レベルは実装済みだが高レベルから到達できず、ピアのドレイン通知も破棄されている。

## 現状

- draft-ietf-webtrans-http2-15 Section 6.13 は「After sending or receiving either a WT_DRAIN_SESSION capsule or HTTP/2 GOAWAY frame, an endpoint MAY continue using the session and MAY open new WebTransport streams. The signal is intended for the application using WebTransport, which is expected to attempt to gracefully terminate the session as soon as possible.」と定め、シグナルはアプリへ渡すことが前提になっている
- 低レベルは実装済みである。`src/bindings/webtransport_h2.cpp` の `H2Session::drain_session` が capsule を送出し、`H2Session::handle_wt_drain_session` が `H2EventType::SessionDraining` を push する。`src/webtransport/webtransport_ext/h2.pyi` には `SESSION_DRAINING` がある
- 高レベルは `h2.Client` / `h2.Server` / `SessionWriter` のいずれにも `drain_session` が無い。`h2.Client.run` と `h2.Server._handle_client` のイベント分岐に `SESSION_DRAINING` が無いため、ピアのドレイン通知はアプリへ届かず破棄される。`on_session_draining` は `src/` に存在しない
- `tests/test_webtransport_h2_stop_sending_drain_session.py` と `tests/prop_isolation_h2.py` / `tests/prop_webtransport_h2.py` は低レベル API で検証している
- `issues/pending/0198` は「h3 に受信通知が無い」を扱い、その設計方針で「高レベル h3.Client に on_session_draining コールバックを追加する (h2 側と対称)」と書いているが、h2 側の高レベルにも API は無い。0198 の現状認識 (「h2 側は実装済み」) は低レベルのみを指している
- 影響: ピアのドレイン通知を高レベル利用者が観測できず、ドレイン期間中に新規ストリームを開き続ける等の非協調動作になる。アプリからドレインを通知する手段も無い

## 設計方針

- `h2.Server` に `on_session_draining(callback)` を追加する。サーバーの既存コールバックが `SessionWriter` を受け取る形に揃え、引数も同じ形にする
- `h2.Client` に `on_session_draining(session_id)` を追加する。クライアントの既存コールバック (addr なし) に揃える
- `SessionWriter.drain_session()` と `h2.Client.drain_session()` を追加し、低レベル `H2Session::drain_session` を呼んで送信待ちをフラッシュする (`close_session` / `stop_sending` と同じ手順)
- `SessionWriter` は 0044 (pending) が型の統一を扱っているため、0044 と実装順が前後する場合は rebase する
- 0198 が h3 側へ同種の API を追加するため、名前と引数の形を h3 と揃える (0198 が h3 の受信のみを対象にしているのに対し、本 issue は h2 の送受信の両方を対象とする)
- 変更対象: `src/webtransport/h2/client.py` / `server.py`、`tests/`、`skills/webtransport-py/SKILL.md`

## 完了条件

- ピアが WT_DRAIN_SESSION を送ると `on_session_draining` が 1 回発火する (`h2.Server` は `SessionWriter`、`h2.Client` は `session_id` を渡す)
- `SessionWriter.drain_session()` と `h2.Client.drain_session()` が WT_DRAIN_SESSION capsule を送出し、ピア側の低レベル API で観測できる
- ドレイン後もセッションを継続して送受信できる (Section 6.13 の MAY continue using the session)。既存挙動を変えない
- コールバック未設定でも例外が起きない
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- `skills/webtransport-py/SKILL.md` の h2 節に追加 API を記載する
- 全テストが通過する

## 解決方法
