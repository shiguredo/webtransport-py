# WebTransport over HTTP/3 と HTTP/3 の高レベル Server で Connection Migration を受け付ける

- Created: 2026-08-27
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-http3-server-connection-migration
- Polished: {YYYY-MM-DD}

## 目的

RFC 9000 Section 9 Connection Migration を `h3.Server` と `http3.Server` の高レベル層で受け付けられるようにする。`quic.Server` は closed/0003 で対応済みだが、h3 / http3 サーバー層は unknown アドレスからの Short header パケットを破棄するため、クライアントの接続移行後に接続が失われる。

## 現状

- `src/webtransport/quic/server.py` は unknown アドレスからのパケットに対し、Long / Short header を判定し、Short header なら既存接続へ順次 `receive` を試すフォールバックを実装済み。既存接続の疎通性を確認できたら `_connections` の `(host, port)` キーを新アドレスに張り替える
- `src/webtransport/h3/server.py` と `src/webtransport/http3/server.py` の `run()` は unknown アドレスのパケットについて `_create_connection` / `_accept_connection` の失敗 (`RuntimeError`) を捕捉して黙って破棄する (closed/0114 で追加された経路)。既存接続へのフォールバックが無い
- 結果、クライアントが接続移行 (アドレス変更) を行うと接続が失われる
- Connection Migration の QUIC 層実装自体は closed/0003 で完了済み

## 設計方針

- `src/webtransport/h3/server.py` と `src/webtransport/http3/server.py` の `run()` を書き換え、unknown アドレスからのパケットに対して `src/webtransport/quic/server.py` の Short header フォールバック実装 (Long / Short 判定 → Long なら新規 accept、Short なら既存接続へ順次 `receive` を試す) を移植する
- 既存接続の再割り当てで h3 / http3 両サーバーの `_clients` (どちらも属性名は同じ `_clients: dict[tuple[str, int], ClientConnection]`) の `(host, port)` キーを新アドレスに張り替える
- 張り替え後は、以降の `_process_quic_events` / `_process_webtransport_events` / `_send_to` / 末尾のタイマー分岐すべてに新 addr を使う (新旧混在で誤配送しないため)
- `on_session_closed` 等の高レベルコールバックに渡す addr も新 addr にする (Migration 発生前後でアプリ観点の identity が入れ替わっても、以後は新 addr を使う)
- RFC 9000 Section 9 の Path Validation (PATH_CHALLENGE / PATH_RESPONSE) の追加は本 issue のスコープ外とする。`quic.Server` の高レベル層も現状は明示的な path validation 待ちを介さず、既存接続の `receive` がバイトを消費できた疎通性で乗り換える方針であり、本 issue はその方針を h3 / http3 に揃える。Path Validation 対応が必要になれば別 issue として起票する

## 補足: h3.Client / http3.Client 側の migrate() 相当 API の要否

- 現状 `migrate()` は `src/webtransport/quic/client.py` の `quic.Client` にのみ実装されている
- `h3.Client` / `http3.Client` には `migrate()` が無い
- e2e テストで h3 / http3 サーバーの Migration 受付を検証するには、クライアント側から Migration を発行する手段が必要
- テスト内で quic 層を直接呼び出して Migration を発行できるならクライアント API 追加は不要。追加する場合は本 issue の範囲に含めて同一 PR で対応する。要否は実装着手時に確定する

## 完了条件

- `h3.Server` / `http3.Server` の `run()` が unknown アドレスからの Short header パケットを既存接続に再割り当てする
- h3 / http3 両サーバーの `_clients` の `(host, port)` キーが新アドレスに張り替わる
- Migration 発生後の以降のコールバック引数 addr が新アドレスに切り替わる
- Migration 後にクライアント↔サーバー間の双方向通信が継続することを確認する e2e テストが追加される (`quic.Server` の Migration テストを h3 / http3 に横展開する)
- `h3.Client` / `http3.Client` の `migrate()` 追加要否が判断され、追加する場合は同一 PR に含める
- 既存の全テストが通る

## 関連 issue

- `issues/closed/0003-add-quic-connection-migration.md` — quic 層の Connection Migration 実装 (本 issue の前提)
- `issues/closed/0114-bug-fix-http3-server-accept-exception.md` — 本 issue で書き換える accept 経路の `RuntimeError` 捕捉分岐を追加した修正
