# WebTransport over HTTP/3 が :protocol "webtransport" トークンを受理する問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-protocol-token
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http3-16 Section 3.2 の MUST「:protocol は webtransport-h3 であること」に反し、カプセルベースプロトコル用トークン "webtransport" で CONNECT された場合もネイティブ HTTP/3 セッションとして受理する問題を修正する。トークンを誤って受理すると、ネイティブ H3 のストリーム先頭シグナル (単方向ストリームタイプ 0x54 / 双方向シグナル値 0x41) とカプセルベースプロトコルの解釈が食い違い、プロトコル混乱を招く。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::end_headers_cb` は `:protocol` として "webtransport-h3" と "webtransport" の両方を受け入れ、どちらもネイティブ H3 セッションとして処理する (SESSION_READY 発火 → `accept_session` で 2xx 応答)
- "webtransport" は draft-ietf-webtrans-http2-15 のアップグレードトークンであり、HTTP/3 上ではカプセルベースプロトコル (draft-16 Section 2.1.2) のトークンとして使われ得る。本実装はネイティブ H3 のみを実装しているため、"webtransport" をネイティブセッションとして受理するのは誤り
- 依存ライブラリ nghttp3 自身も `:protocol` の "webtransport-h3" と "webtransport" の両方で WebTransport フラグを立てる (`_deps/nghttp3/webtransport/source/lib/nghttp3_http.c`)。このため受信側の拒否はアプリ (本ライブラリ) 側で行う必要がある
- クライアント側の送出は "webtransport-h3" のみで正しい。両トークン受理は「クライアント送出を webtransport-h3 に切り替えた際」の互換措置として追加された経緯があるが、互換を要するクライアントは現存しない

## 設計方針

- `end_headers_cb` の `:protocol` 判定を "webtransport-h3" のみに限定し、**"webtransport" の CONNECT は C++ 側で自動的に拒否応答を返す** (応答を返さないと高レベル層では SESSION_READY が発火せずアプリが応答できないため、クライアントが応答待ちでハングし、未応答の CONNECT ストリームが残留する)。実装は「`is_connect` かつ `:protocol` が "webtransport"」を検出して既存の `reject_session` を呼ぶ追加分岐とする。既存の Origin 検証失敗分岐 (403) と同じ後始末 (`pending_qpack_blocked_fin_stream_ids_` と `pending_headers_` からの除去) を行ってから return する
- 拒否応答のステータスコードは 405 とする。仕様上の根拠は「target resource が WebTransport をサポートしない場合の 405 SHOULD」(draft-ietf-webtrans-http3-16 Section 3.2。draft-ietf-webtrans-http2-15 Section 3.2 にも同様の 405 SHOULD がある) に準じる (":protocol: webtransport" の HTTP/3 上での応答コードは draft-ietf-webtrans-http3-16 では未規定のため、405 を選択する)。nghttp3 は非 2xx 応答を受信すると WebTransport アップグレード拒否として扱うため整合する
- "webtransport" の CONNECT を拒否するテストを追加する。クライアント API の `connect()` は :protocol を "webtransport-h3" に固定して送出するため、テストでは QPACK 手動エンコード等で ":protocol: webtransport" の CONNECT をサーバーへ注入する手段を用意する
- 変更内容を CHANGES.md の `## develop` に [FIX] として記載する

## 完了条件

- `:protocol: webtransport` の CONNECT リクエストがネイティブセッションとして受理されず、405 応答が返る
- 拒否後にクライアントが応答待ちでハングしない (ストリームが残留しない)
- テストが追加され通る
