# h3 / http3 サーバーの高レベル API を拡充する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-http3-server-capabilities
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/3 (`h3.Server`) と HTTP/3 (`http3.Server`) の高レベルサーバーに欠落している 2 つの機能を追加する。

- 項目 A: `h3.Server` のセッション拒否高レベル API
- 項目 B: `h3.Server` / `http3.Server` の Connection Migration 対応

**注意**: 本 issue は目的が独立した 2 項目 (draft-ietf-webtrans-http3-16 Section 3.2 のセッション拒否と RFC 9000 Section 9 の Connection Migration) を暫定的にまとめている。分離作業は本 issue のスコープ外であり、別途 polish-issue-deep で 2 つの issue に分割してから実装に進むこと。

## 現状

### 項目 A: h3.Server のセッション拒否 API 未実装

- `src/webtransport/h3/server.py` の `Server` は SESSION_READY 受信時に無条件で `client.webtransport_session.accept_session(session_id)` を呼び (該当ファイルの SESSION_READY 分岐)、アプリはセッションを拒否できない
- draft-ietf-webtrans-http3-16 Section 3.2 は 403 / 405 拒否を規定しており、拒否手段がないと仕様の拒否シナリオをアプリで実現できない
- 一方 `h2.Server` は `on_session_request` コールバック API を提供済み (`src/webtransport/h2/server.py` の `on_session_request` プロパティと SESSION_READY 分岐、および `session.reject_session(event.session_id, status)` の呼び出し)。CHANGES.md develop の `[ADD] WebTransport over HTTP/2 高レベル Server にセッション拒否 API (on_session_request コールバック) を追加する`、および `issues/closed/0134-add-h2-server-reject-session-api.md` で完了済み
- したがって現状は「h2 と h3 の高レベル Server の API が非対称 (h2 のみ対応済み)」の状態である

### 項目 B: h3 / http3 サーバーが Connection Migration 未対応

- `src/webtransport/quic/server.py` は unknown アドレスからの short header パケットに対し、既存接続へ順次 `receive` を試すフォールバックを実装済み (Long / Short header 判定と既存接続再割り当て経路)
- 一方 `src/webtransport/h3/server.py` と `src/webtransport/http3/server.py` は unknown アドレスからのパケットについて、`_create_connection` の失敗 (RuntimeError) を捕捉して黙って破棄する (`issues/closed/0114-bug-fix-http3-server-accept-exception.md` で追加された経路)。既存接続へフォールバックする経路がないため、クライアントが接続移行 (アドレス変更) を行うと接続が失われる
- Connection Migration の QUIC 層実装自体は `issues/closed/0003-add-quic-connection-migration.md` で完了済み。本項目はその挙動を h3/http3 サーバー層まで拡張する残タスクにあたる

## 設計方針

### 項目 A: h3.Server にセッション拒否 API を追加

- `h3.Server` に `h2.Server` と対称の `on_session_request` コールバック API を追加する
- シグネチャは `h2.Server.on_session_request` と同一とする
  - 受信するもの: `session_id: int`, `headers: list[tuple[str, str]]`
  - 戻り値: `None` (accept) または `int` (300-599 の HTTP status code。非 2xx でセッションを拒否する)
  - 戻り値の型・範囲外は `ValueError` / `TypeError` を送出する (h2 側と同一の検証)
- SESSION_READY 分岐で `on_session_request` を呼び、戻り値に応じて `webtransport_session.accept_session()` / `webtransport_session.reject_session(session_id, status)` を呼び分ける
- 拒否時は `on_session_ready` を発火しない (accept 経路のみ発火)
- `h3.Session.reject_session` の低レベル API が既に存在するかを実装時に確認する。存在しなければバインディング側 (`src/bindings/webtransport_h3.cpp`) にも追加する
- 本項目のスコープは h3.Server のみ。http3.Server は WebTransport セッション概念を持たない汎用 HTTP/3 サーバーであり、リクエスト単位の拒否 (403/405) は既存の `submit_response` で実現できるためスコープ外
- Origin 検証は既に H3 側で `Config.allowed_origins` / `verify_origin` として実装済み (`src/webtransport/h3.pyi` の Config、`src/bindings/webtransport_h3.cpp` の verify_origin)。本項目では Origin 検証には手を加えない

### 項目 B: h3 / http3 サーバーの Connection Migration 対応

- `src/webtransport/h3/server.py` と `src/webtransport/http3/server.py` の run() で、unknown アドレスからのパケットを受け取ったときの処理を書き換える
- 具体的には `src/webtransport/quic/server.py` の short header フォールバック実装 (Long / Short header 判定 → Long header なら新規 accept、Short header なら既存接続へ順次 `receive` を試す) を h3/http3 サーバーに移植する
- 既存接続の再割り当てで `self._clients` 相当の辞書のキー (host, port タプル) を新しいアドレスに張り替える処理を追加する
- RFC 9000 Section 9 の Path Validation (PATH_CHALLENGE / PATH_RESPONSE) の追加は本項目のスコープ外とする (quic.Server も現状は path validation を実装せず、疎通性のみで乗り換えている。将来 quic.Server 側で path validation を追加する時点で h3/http3 側も揃える)
- 実装対象は `_create_connection` を呼ぶ経路 (h3/server.py と http3/server.py の run() 内、closed/0114 で追加された RuntimeError 捕捉分岐) を修正する

## 完了条件

- 項目 A
  - `h3.Server` に `on_session_request` コールバックが追加され、`h2.Server.on_session_request` と同一シグネチャ・同一検証で動作する
  - 拒否 (300-599) 時に `h3.Session.reject_session` が呼ばれ、`on_session_ready` は発火しない
  - accept 経路が既存の e2e テストと互換であることを確認する e2e テストを追加する
  - 拒否経路のテストを追加する (h2 側の対応する拒否テストを参考にする)
- 項目 B
  - `h3.Server` / `http3.Server` の run() が unknown アドレスからの Short header パケットを既存接続に再割り当てする
  - 既存接続のキー (host, port) が新アドレスに張り替わる
  - Migration 後にクライアント↔サーバー間の双方向通信が継続することを確認する e2e テストを追加する (quic.Server の Migration テストを h3/http3 に横展開する)
- 既存の全テストが通る

## 関連 issue

- `issues/closed/0003-add-quic-connection-migration.md` — 本 issue の項目 B の前提となる quic 層の Connection Migration 実装
- `issues/closed/0114-bug-fix-http3-server-accept-exception.md` — 本 issue の項目 B で書き換える accept 経路の RuntimeError 捕捉分岐を追加した修正
- `issues/closed/0134-add-h2-server-reject-session-api.md` — 本 issue の項目 A で対称の API を追加する h2 側の実装 (シグネチャ・検証の参考)
