# HTTP/3 のプロトコルエラーが無音で握りつぶされる問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-error-path-silent
- Polished: {YYYY-MM-DD}

## 目的

ピアが不正フレームを送るなどのプロトコルエラーが発生したときに、アプリケーションがエラーを検知できない問題を修正する。現在は nghttp3 のエラーを黙って握りつぶし、接続が無音で死んだままになる。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` は `nghttp3_conn_read_stream2` のエラーを `return 0` のみで握りつぶす。エラーイベントも `closed_` も立てない
- `Http3Connection::get_streams_to_send` も `writev_stream` のエラーを break のみで無視する
- 高レベル層 (`src/webtransport/http3/client.py` の `Client.run` / `src/webtransport/http3/server.py` の `Server.run`) も戻り値を無視するため、アプリは接続がプロトコルエラーで死んだことを検知できない
- HTTP/2 側 (`src/bindings/http2.cpp` の `receive`) は closed_ になる分まだ検知可能だが、エラーコード・理由は通知されない

## 設計方針

- エラー時にエラーイベント (エラーコード・メッセージ付き) を push するか、`closed_` を立てて高レベル層が終了処理できるようにする
- 高レベル層でエラーを通知・終了処理する

## 完了条件

- 不正フレーム受信時にエラーがイベントまたは例外としてアプリへ通知される
- 高レベル層の run() がハングせず終了する
- テストが追加される
