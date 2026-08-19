# WebTransport over HTTP/2 のクライアントが 2xx 非 200 応答をセッション確立として扱わない問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-client-2xx-response
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 3.2「A WebTransport session is established when the server sends a 2xx response」に反し、クライアントが 200 応答のみを確立として扱う問題を修正する。201 等の 2xx 応答では connect が SESSION_READY を待ち続けてハングする。

## 現状

- `src/bindings/webtransport_h2.cpp` の `on_frame_recv_callback` は `:status` が "200" の場合のみ成功扱いとする
- 201 等の 2xx 応答では is_success にならず、セッションエントリも削除されない (非 2xx 分岐にも該当しない) ため、高レベル層 `src/webtransport/h2/client.py` の `Client.connect` は SESSION_READY を待ち続けて永久ブロックする

## 設計方針

- `:status` の先頭文字が '2' であることを確立条件とする (HTTP/3 側 `end_headers_cb` は `status[0] != '2'` で判定している方式と同様)
- 2xx 非 200 応答のテストを追加する

## 完了条件

- 201 等の 2xx 応答でセッションが確立として扱われ、connect が成功する
- テストが追加される
