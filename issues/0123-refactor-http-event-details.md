# HTTP/2・HTTP/3 低レベル API の観測性と細部を改善する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-http-event-details
- Polished: {YYYY-MM-DD}

## 目的

HTTP/2・HTTP/3 低レベル API (`http2.Connection` / `http3.Connection`) のイベント情報の不足と細部の不整合を改善する。受信イベントから必要な情報を観測できない箇所を補い、エラーコードの整合を取る。

## 現状

- **PING の opaque data がイベントに含まれない**: `src/bindings/http2.cpp` の `on_frame_recv_callback` は受信 PING をイベント種別のみで通知し、8 バイトのデータを渡さない。RTT 測定・疎通確認用途に使えない
- **WINDOW_UPDATE の増分値がイベントに含まれない**: 同コールバックの WINDOW_UPDATE イベントに RFC 9113 Section 6.9.1 の Window Size Increment が含まれず、観測できない
- **受信トレーラ・1xx レスポンスが Headers イベントと区別できない**: `src/bindings/http2.cpp` と `src/bindings/http3.cpp` はトレーラ / 1xx を通常の HEADERS イベントとして積む。RFC 9114 Section 4.2 の 1xx セマンティクスを扱うには不足
- **HTTP/3 の close_stream デフォルト error_code が H3_NO_ERROR でない**: `src/bindings/http3.cpp` のバインディングは `close_stream` のデフォルト error_code を 0 にしているが、RFC 9114 Section 8.1 の H3_NO_ERROR は 0x0100。HTTP/3 のエラーコード空間と不整合
- **HTTP/3 の goaway(id) の id 引数が無視される**: `src/bindings/http3.cpp` の `Http3Connection::goaway` は引数 id を受け取るが `nghttp3_conn_shutdown` には渡さず、GOAWAY ID は nghttp3 が内部算出する。死んだ引数になっている
- **HTTP/2 の data_source_read_callback が O(n²)**: `src/bindings/http2.cpp` の `Http2Connection::data_source_read_callback` は部分コピーごとに `front.data.erase` で全残データをシフトする。大バッファを max_frame_size 刻みで送出するとコピーコストが二乗で増える

## 設計方針

- PING イベントに opaque data、WINDOW_UPDATE イベントに増分値を追加する
- トレーラ・1xx を区別できるイベント種別または識別情報を追加する
- `close_stream` のデフォルト error_code を H3_NO_ERROR (0x0100) に変更する (後方互換の観点からデフォルト値変更の影響を確認する)
- `goaway` の id 引数を意味のあるものにするか、引数を削除する
- `data_source_read_callback` のバッファ管理をオフセット方式に変えて O(n²) を解消する

## 完了条件

- 各項目が対応され、対応分のテストが追加される
- 全テストが通る
