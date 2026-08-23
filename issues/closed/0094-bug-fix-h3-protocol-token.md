# WebTransport over HTTP/3 が :protocol "webtransport" トークンを受理する問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-nonstandard-protocol-token
- Polished: 2026-08-18

## reopened にした理由

- 本 issue の実装 (2026-08-23) は `:protocol: "webtransport"` の CONNECT を 501 で拒否したが、実ブラウザ (Chromium / WebKit) の WebTransport クライアント (Shiguredo WebTransport DevTools) は現時点でも `:protocol: "webtransport"` を送信する (実測: `recv_header_cb` で `:protocol value=webtransport` を観測)。その結果、実ブラウザの WebTransport over HTTP/3 セッション確立がすべて 501 で拒否され、`tests/browser/` のブラウザ E2E テストが全滅した。"webtransport" は draft-ietf-webtrans-http2-15 のアップグレードトークンであり、HTTP/2 ベースのカプセルプロトコル (WebTransport over HTTP/2) と将来の HTTP/3 カプセルプロトコルにも使われる可能性があるため、本ライブラリ (ネイティブ HTTP/3 実装) が両方のトークンをネイティブセッションとして受け入れる
- 仕様上の正しさ (draft-16 Section 3.2 の MUST「:protocol は webtransport-h3」) と実ブラウザの現状の乖離であり、本ライブラリとしては実ブラウザ互換を優先して "webtransport" も "webtransport-h3" と同様に受理する

## 目的

draft-ietf-webtrans-http3-16 Section 3.2 の MUST「:protocol は webtransport-h3 であること」に反し、カプセルベースプロトコル用トークン "webtransport" で CONNECT された場合もネイティブ HTTP/3 セッションとして受理する問題を修正する。トークンを誤って受理すると、ネイティブ H3 のストリーム先頭シグナル (単方向ストリームタイプ 0x54 / 双方向シグナル値 0x41) とカプセルベースプロトコルの解釈が食い違い、プロトコル混乱を招く。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::end_headers_cb` は `:protocol` として "webtransport-h3" と "webtransport" の両方を受け入れ、どちらもネイティブ H3 セッションとして処理する (SESSION_READY 発火 → `accept_session` で 2xx 応答)
- "webtransport" は draft-ietf-webtrans-http2-15 のアップグレードトークンであり、HTTP/3 上ではカプセルベースプロトコル (draft-16 Section 2.1.2) のトークンとして使われ得る。本実装はネイティブ H3 のみを実装しているため、"webtransport" をネイティブセッションとして受理するのは誤り
- 依存ライブラリ nghttp3 自身も `:protocol` の "webtransport-h3" と "webtransport" の両方で WebTransport フラグを立てる (`_deps/nghttp3/webtransport/source/lib/nghttp3_http.c`)。このため受信側の拒否はアプリ (本ライブラリ) 側で行う必要がある
- クライアント側の送出は "webtransport-h3" のみで正しい。両トークン受理は「クライアント送出を webtransport-h3 に切り替えた際」の互換措置として追加された経緯があるが、互換を要するクライアントは現存しない

## 設計方針

- `end_headers_cb` の `:protocol` 判定を "webtransport-h3" のみに限定し、**"webtransport" の CONNECT は C++ 側で自動的に拒否応答を返す** (応答を返さないと高レベル層では SESSION_READY が発火せずアプリが応答できないため、クライアントが応答待ちでハングし、未応答の CONNECT ストリームが残留する)。実装は「`is_connect` かつ `:protocol` が "webtransport"」を検出して既存の `reject_session` を呼ぶ追加分岐とする。既存の Origin 検証失敗分岐 (403) と同じ後始末 (`pending_qpack_blocked_fin_stream_ids_` と `pending_headers_` からの除去) を行ってから return する
- 拒否応答のステータスコードは 501 とする。Extended CONNECT を広告したサーバーが未サポートの `:protocol` を受信した場合は 501 で応答する SHOULD (RFC 9220 Section 3。draft-ietf-webtrans-http3-16 Section 3.1 が RFC 9220 を規範的に参照)。draft-16 Section 3.2 の 405 SHOULD は webtransport-h3 で target resource が非サポートのケースに限定され、本ケースには適用されない (実装時に 405 → 501 へ変更。ユーザー確認済み)。nghttp3 は非 2xx 応答を受信すると WebTransport アップグレード拒否として扱うため整合する
- "webtransport" の CONNECT を拒否するテストを追加する。クライアント API の `connect()` は :protocol を "webtransport-h3" に固定して送出するため、テストでは QPACK 手動エンコード等で ":protocol: webtransport" の CONNECT をサーバーへ注入する手段を用意する
- 変更内容を CHANGES.md の `## develop` に [FIX] として記載する

## 完了条件

- `:protocol: webtransport` の CONNECT リクエストがネイティブセッションとして受理されず、501 応答が返る
- 拒否後にクライアントが応答待ちでハングしない (ストリームが残留しない)
- テストが追加され通る

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `end_headers_cb` に `is_capsule_protocol` (":protocol: webtransport") の分岐を追加し、`reject_session(stream_id, 501)` で拒否する。後始末 (pending_qpack_blocked_fin_stream_ids_ / pending_headers_ の除去) は既存の Origin 検証失敗分岐 (403) と同型。:protocol 判定は "webtransport-h3" のみを受理
- `src/bindings/webtransport_h3.cpp` / `h`: テスト専用アクセサ `_last_reject_status_code()` を追加 (reject_session が送出したステータスコードを返す。未送出時は None)
- `tests/test_webtransport_h3_protocol_token.py` (新規): QPACK 手動エンコードで任意の :protocol の CONNECT を注入するテスト 3 件 (webtransport が 501 で拒否 / クライアントセッション削除 / webtransport-h3 受理の回帰)
