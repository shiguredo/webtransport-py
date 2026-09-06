# WebTransport over HTTP/3 が GOAWAY 受信で接続を閉じたものとして扱い、高レベル API が H3_GENERAL_PROTOCOL_ERROR で切断する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-goaway-closes-session
- Polished: {YYYY-MM-DD}

## 目的

`H3Session::shutdown_cb` は nghttp3 の shutdown コールバックで発火するが (GOAWAY 受信時に発火する)、内部で `closed_ = true` を立ててしまう。高レベル `h3.Client` / `h3.Server` は `is_closed()` を「プロトコルエラー」と解釈して `_close_on_protocol_error()` から `H3_GENERAL_PROTOCOL_ERROR` (0x0101) で QUIC の `CONNECTION_CLOSE` を送出する。draft-ietf-webtrans-http3-16 Section 4.7 は「GOAWAY 受信後もセッションを継続してよい」と定めており、RFC 9114 Section 5.2 は graceful shutdown 完了時に `H3_NO_ERROR` を使う SHOULD がある。正当な graceful shutdown をプロトコル違反として切断してしまう仕様違反。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::shutdown_cb` は `session->closed_ = true;` のみ
- `src/webtransport/h3/client.py` の `Client.run` は `if self._webtransport_session is not None and self._webtransport_session.is_closed(): ... await self._close_on_protocol_error(); ...` で `H3_GENERAL_PROTOCOL_ERROR` (`src/webtransport/http3/constants.py` の 0x0101) を送出
- `src/webtransport/h3/server.py` の `Server.run` にも同型の分岐がある
- 対照: `src/bindings/http3.cpp` の `Http3Connection::shutdown_cb` は `GoAway` イベントを積むだけで `closed_` を立てない → 同じ nghttp3 コールバックの解釈が 2 ファイルで正反対
- `_deps/nghttp3/webtransport/source/lib/nghttp3_conn.c` で `shutdown` コールバックは GOAWAY 受信時に発火する (`conn->flags |= NGHTTP3_CONN_FLAG_GOAWAY_RECVED;` の直後)
- draft-16 Section 4.7 (refs 925-928 行) 「After sending or receiving either a WT_DRAIN_SESSION capsule or a HTTP/3 GOAWAY frame, an endpoint MAY continue using the session: it MAY open new WebTransport streams and MAY send new datagrams」
- RFC 9114 Section 5.2 「An endpoint that completes a graceful shutdown SHOULD use the H3_NO_ERROR error code when closing the connection」
- 同一クラス内での矛盾: `H3Session::unblock_stream` の doc「GOAWAY 受信 (graceful shutdown) 後も既存ストリームのフロー制御ブロック操作は有効なため、closed_ は見ない」は GOAWAY 後の継続を前提にしている
- h3 側に GOAWAY を扱うテストは存在しない (grep で goaway / shutdown の出現 0 件)

## 設計方針

- `H3Session::shutdown_cb` は `closed_` を立てない。代わりに `SessionDraining` 相当のイベント (h2 側の `SESSION_DRAINING` と対称) を積む
- 新規イベントタイプを `H3EventType` に追加 (末尾追加ではなく、下位互換を維持しない CODEBASE 方針に従い順序を再検討する)
- 高レベル `Client` / `Server` は `SessionDraining` を `on_session_draining` コールバックとして通知する。既存セッションの送受信は継続する
- `is_closed()` の doc を「nghttp3 の負値 return (接続エラー) 時のみ真」に訂正する
- draft-16 §6 の close_session 順序準拠 (別 issue 予定) と協調する必要があるため、実装時は close 系の状態機械と合わせて検討する

## 完了条件

- GOAWAY 受信後もセッションが継続でき、新規ストリームの open と datagram の送信が可能なこと
- 高レベル層が `H3_GENERAL_PROTOCOL_ERROR` を送出しないこと
- `on_session_draining` (仮) コールバックで GOAWAY 受信をアプリに通知できること
- `tests/` に GOAWAY 受信時の継続と `on_session_draining` の発火を検証するテストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
