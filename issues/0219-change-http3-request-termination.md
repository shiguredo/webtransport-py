# http3.Client.request() がリクエストを終端しない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/change-http3-request-termination
- Polished: 2026-09-15

## 目的

`src/webtransport/http3/client.py` の `Client.request` はリクエストヘッダーを送るだけで fin を送らない。QUIC のストリームは fin を送るまで終わらないため、`request()` を呼んだだけではリクエストが完了せず、対向のサーバーはリクエストの終端を検知できない。終端を待って応答するサーバー (`examples/http3/server.py`、`tests/test_e2e_http3.py` の `test_server_on_stream_end_fires_for_bodyless_request`) は応答を返せない。加えて現状は「終端は利用者が `Client.send_data(..., fin=True)` で行う」という分担が docstring にも `SKILL.md` にも書かれておらず、終端し忘れが無言のハングにつながる。

## 現状

- `Client.request` は `Http3Connection.submit_request` を呼んでから `Client._send_pending` を呼ぶだけで、fin を送出しない
- `src/bindings/http3.cpp` の `read_data_cb` は、当該ストリームの送信バッファが空の場合に `NGHTTP3_ERR_WOULDBLOCK` を返す。`Http3Connection::submit_request` は空のバッファエントリを作るため、nghttp3 へ fin が伝わらない
- 利用者が `Client.send_data(stream_id, b"", fin=True)` を呼べば終端できる。既存のテストはこの手順を前提にしており、`tests/test_e2e_http3.py` に `client.send_data` の呼び出しが 10 箇所あり、うち 6 箇所が `request()` の直後に終端だけを目的とした `b""` の送信である (`tests/test_e2e_http3_throughput.py` にも同種の呼び出しが 1 箇所ある)
- `src/webtransport/http2/client.py` の `Client.request` は `Client.send_data(..., eof=True)` で常に終端する。層間で契約が非対称になっている (`CHANGES.md` の `## develop` に HTTP/2 側の変更が記載されている)
- `examples/http3/client.py` は `Client.request("GET", "/")` の後に終端せず、`examples/http3/server.py` は `on_stream_end` でだけ応答する。加えて接続先が一致していない (`examples/http3/client.py` は `www.google.com` の 443 番、`examples/http3/server.py` は 4433 番で待ち受ける) ため、終端を直してもこの 2 本は組として通信できない。接続先と `verify_peer` の整合は別 issue (0225) が担当する
- HTTP/3 には HTTP/2 の END_STREAM フラグが無く、終端は fin (`NGHTTP3_DATA_FLAG_EOF`) で表現する (RFC 9114 Appendix A.2)

## 設計方針

- `Client.request` に `body: bytes | None = None` を追加し、常にリクエストを終端する。`body` が `None` のときは空ボディを送って終端する
- 終端し忘れが構造的に起きないことを優先する。`request()` を呼んだのにリクエストが終わらない状態は、サーバーが応答を返さないまま無言でハングする最も避けたい失敗である。`body` を省略したときに終端しない設計はこの失敗を残すため採らない
- 分割送りは高レベルでは提供しない。Sans-IO の `http3.Connection.submit_request` と `http3.Connection.send_data` を使う手順を docstring に http2 と同文面で書き、低レベル層は状態が利用者の手元にあるため分割送りに向くことを示す。この判断の理由を docstring に残す
- 終端後の追加送信は送出されない (fin 送出後に nghttp3 の送信キューから外れるため)。http2 と同じくエラーにはせず、docstring で不活性を明示する
- 後方互換は `CODEBASE.md` の「下位互換を維持しないこと」に従い維持しない。`CHANGES.md` に `[CHANGE]` として追記する
- 失敗の伝播は別 issue (0213) が扱う。0213 を先に実装し、その失敗分岐 (`Client._quic_connection.open_stream` の負値と `Http3Connection.submit_request` の false で `-1` を返し、開設済みストリームを `Client._quic_connection.reset_stream` でリセットする) を維持したまま `body` と終端を追加する
- `examples/http3/client.py` と `examples/http3/server.py` は本 issue では変更しない。接続先の整合と組としての動作確認は 0225 が担当する

## 完了条件

- `Client.request` が常にリクエストを終端し、`body` を指定した場合はその内容を送ってから終端する
- `body` を省略したリクエストでも `on_stream_end` が発火する
- 0213 の失敗分岐 (失敗時 `-1` と `Client._quic_connection.reset_stream`) が維持されている
- docstring (クラスの Usage と `Client.request` / `Client.send_data`) が新しい契約に一致し、終端後の追加送信が送出されないことと、分割送りは Sans-IO の `http3.Connection.submit_request` / `http3.Connection.send_data` を使うことが書かれている
- `skills/webtransport-py/SKILL.md` の `http3.Client` の `request()` の記載 (引数と終端契約) と、HTTP/2 節にある http3 との比較文が新しい契約に一致する
- 既存テストが新しい契約に移行され、全テストが通過する

## 解決方法

- `src/webtransport/http3/client.py` の `Client.request` に `body: bytes | None = None` を第 4 引数として追加し、`Client.send_data(stream_id, payload, fin=True)` を呼ぶ形に変更する (`payload` は `body` が `None` なら `b""`)
- `Client.request` と `Client.send_data` の docstring、およびクラス docstring の Usage を新しい契約に書き換える (クラス docstring は現在 `request()` の後に `run()` を案内するだけで終端に触れていない)
- `skills/webtransport-py/SKILL.md` の `http3.Client` の `request()` と、HTTP/2 節の比較文を更新する
- `CHANGES.md` の `## develop` に `[CHANGE]` を追記する
- `tests/test_e2e_http3.py` の既存テストを新しい契約に移行する
  - `test_server_client_post_with_body` / `test_large_post_body` は `body=` で送る形に変更する
  - `test_server_on_stream_end_fires_after_body` はクライアントが分割送り (`fin=False` を挟む) を検証しているため、Sans-IO の `http3.Connection.submit_request` / `http3.Connection.send_data` を使う形に移す。高レベルで分割送りが提供されないことを検証するテストは追加しない (契約上、追加送信は送出されない)
  - `request()` の後に `send_data(stream_id, b"", fin=True)` を呼んでいる箇所 (二重終端) を削除する
  - `tests/test_e2e_http3_throughput.py` の該当箇所も同様に移行する
- `tests/test_e2e_http3.py` に GET とボディ付きリクエストのテストを追加する
