# HTTP/3 高レベル層で on_stream_end が二重通知される問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-stream-end-double-fire
- Polished: {YYYY-MM-DD}

## 目的

ボディなしレスポンス (204 / 304 等) の受信時に `on_stream_end` コールバックが 2 回呼ばれる問題を修正する。集計系アプリで重複カウントが発生する。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection::end_headers_cb` は fin=1 のとき STREAM_END イベントを積む
- 高レベル層 `src/webtransport/http3/client.py` の `Client.run` は (a) HTTP/3 層の STREAM_END イベントと (b) QUIC 層の FIN (`finished_streams`) の両方で `on_stream_end` を呼ぶ
- HEADERS + fin が 1 チャンクで届くボディなしレスポンスで両経路が発火し、2 回呼ばれる
- `tests/test_e2e_http3.py` の `test_stream_end_callback` はボディ付きレスポンスのみでこの経路を踏んでいないため検出できない

## 設計方針

- STREAM_END の通知経路を 1 つに一本化する (低レベル仕様と高レベル実装のどちらに合わせるかは設計判断)
- ボディなしレスポンスのテストを追加する

## 完了条件

- ボディなしレスポンスで `on_stream_end` が 1 回だけ呼ばれる
- テストが追加される
