# WebTransport over HTTP/3 が GOAWAY 受信で接続を閉じたものとして扱い、高レベル API が H3_GENERAL_PROTOCOL_ERROR で切断する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-goaway-closes-session
- Polished: 2026-09-07

## 目的

`H3Session::shutdown_cb` は nghttp3 の shutdown コールバックで発火するが (GOAWAY 受信時に発火する)、内部で `closed_ = true` を立ててしまう。高レベル `h3.Client` / `h3.Server` は `is_closed()` を「プロトコルエラー」と解釈して `_close_on_protocol_error()` から `H3_GENERAL_PROTOCOL_ERROR` (0x0101) で QUIC の `CONNECTION_CLOSE` を送出する。draft-ietf-webtrans-http3-16 Section 4.7 は「GOAWAY 受信後もセッションを継続してよい」と定めており、RFC 9114 Section 5.2 は graceful shutdown 完了時に `H3_NO_ERROR` を使う SHOULD がある。正当な graceful shutdown をプロトコル違反として切断してしまう仕様違反。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::shutdown_cb` は `session->closed_ = true;` のみ
- `src/webtransport/h3/client.py` の `Client.run` は `if self._webtransport_session is not None and self._webtransport_session.is_closed(): ... await self._close_on_protocol_error(); ...` で `H3_GENERAL_PROTOCOL_ERROR` (`src/webtransport/http3/constants.py` の 0x0101) を送出
- `src/webtransport/h3/server.py` の `Server.run` にも同型の分岐がある
- 対照: `src/bindings/http3.cpp` の `Http3Connection::shutdown_cb` は `GoAway` イベントを積むだけで `closed_` を立てない → 同じ nghttp3 コールバックの解釈が 2 ファイルで正反対
- `_deps/nghttp3/webtransport/source/lib/nghttp3_conn.c` で `shutdown` コールバックは GOAWAY 受信時に発火する (`conn->flags |= NGHTTP3_CONN_FLAG_GOAWAY_RECVED;` の直後)
- draft-16 Section 4.7 (refs 924-926 行) 「After sending or receiving either a WT_DRAIN_SESSION capsule or a HTTP/3 GOAWAY frame, an endpoint MAY continue using the session: it MAY open new WebTransport streams and MAY send new datagrams」
- RFC 9114 Section 5.2 「An endpoint that completes a graceful shutdown SHOULD use the H3_NO_ERROR error code when closing the connection」
- 同一クラス内での矛盾: `H3Session::unblock_stream` の doc「 GOAWAY 受信 (graceful shutdown) 後も既存ストリームのフロー制御ブロック操作は有効なため、closed_ は見ない」は GOAWAY 後の継続を前提にしている
- h3 系テスト (`tests/test_webtransport_h3*.py` / `tests/test_e2e_webtransport_h3.py`) に GOAWAY 受信を扱うテストは存在しない (当該パスでの `goaway` / `GOAWAY` / `shutdown_cb` の出現 0 件。素の `shutdown` 単語は QUIC 系と衝突するため対象外)

## 設計方針

- `H3Session::shutdown_cb` は `closed_` を立てない。代わりにコネクション単位の新規イベント `H3EventType::GoAway` (GOAWAY ID 付き) を積む。セッション単位の `SessionDraining` 流用は選ばない (h2 の `SESSION_DRAINING` は `WT_DRAIN_SESSION` カプセル受信用であり、GOAWAY と仕様上の区別があるため。0122 項目 1 の所有物と衝突させない)
- 新規イベントは `H3EventType` の末尾に追加し、`h3.pyi` の `EventType` にも公開する (0133 / 0140 の確立規約に従い既存値の数値を変えない。CODEBASE の破壊許容は行使しない)。`bind_webtransport_h3` の enum 登録も追加する
- 高レベル `Client` / `Server` は `GoAway` を `on_goaway` コールバックとして通知する。シグネチャは Client が `(goaway_id)`、Server が `(goaway_id, addr)` とし、既存の Client / Server 差 (後者が addr を取る) に合わせる。発火は初回受信のみとし、重複 GOAWAY の多重発火は抑止する。既存セッションの送受信は継続する
- 高レベル `Client.run` / `Server.run` の `is_closed()` 分岐は GOAWAY では発火させない (除外する)。`is_closed()` の doc を再定義する (nghttp3 の負値 return = 接続エラー時のみ真。GOAWAY 受信では真にならない)
- closed issue 0131 の「`shutdown_cb` は変更しない」前提を本 issue が覆すことを明記する。0131 由来の接続エラー注入テスト (`is_closed()` 真) との回帰両立を完了条件に含める
- 0170 (h2 版) とは単位・命名を GoAway 系で揃える方向とし、0170 側の `SessionDraining` 再利用案は本 issue 確定後に見直す。`drain_session` 送信側は対象外とする (0122 の所有)

## 対象・対象外

- 対象: `src/bindings/webtransport_h3.cpp` (`shutdown_cb` と enum 登録) / `src/bindings/webtransport_h3.h` (`H3EventType::GoAway` と `is_closed()` doc) / `src/webtransport/h3.pyi` (`GOAWAY` 公開) / `src/webtransport/h3/client.py` と `server.py` (`on_goaway` と `is_closed()` 分岐除外) / `tests/test_webtransport_h3_goaway.py` (新規、低レベル) と `tests/test_e2e_webtransport_h3.py` (高レベル) / `CHANGES.md`
- 対象外: `drain_session` 送信側 / `http3.cpp` / `http2.cpp` / `WT_DRAIN_SESSION` 経路 (0122 の所有)

## 完了条件

- GOAWAY 受信後に `is_closed()` が偽のままであり、既存セッションで新規ストリームの open と datagram の送信が可能なこと (低レベル Sans-IO で確認する)
- 高レベル層が `H3_GENERAL_PROTOCOL_ERROR` を送出しないこと
- 初回 GOAWAY 受信で `on_goaway` が 1 回発火すること
- 0131 由来の接続エラー注入テストが引き続き `is_closed()` 真を維持すること (回帰両立)
- `tests/test_webtransport_h3_goaway.py` (新規) に GOAWAY フレーム注入による継続テスト (制御ストリームへ注入し、`is_closed()` 偽 + `GoAway` イベント + open / send 可を表明) を追加し、`tests/test_e2e_webtransport_h3.py` に `H3_GENERAL_PROTOCOL_ERROR` 不送出と `on_goaway` 発火のテストを追加すること
- 既存のテスト全 834 件が引き続き通過すること
