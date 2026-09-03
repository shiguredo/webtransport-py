# HTTP/2・HTTP/3 低レベル API の観測性と細部を改善する

- Created: 2026-08-18
- Completed: 2026-09-03
- Branch: feature/refactor-http-event-details
- Polished: {YYYY-MM-DD}

## 目的

HTTP/2・HTTP/3 低レベル API (`http2.Connection` / `http3.Connection`) のイベント情報の不足と細部の不整合を改善する。受信イベントから必要な情報を観測できない箇所を補い、エラーコードの整合を取る。

## 現状

- **PING の opaque data がイベントに含まれず、ACK PING も観測できない**: `src/bindings/http2.cpp` の `on_frame_recv_callback` は ACK フラグなしの PING のみをイベント種別のみで通知し、8 バイトの opaque data (RFC 9113 Section 6.7) を渡さない。ACK フラグ付き PING はイベントにならない。さらに `Http2Connection::ping` は `nghttp2_submit_ping` に NULL を渡すため、送信側も opaque data を設定できない。RTT 測定・疎通確認用途に使えない
- **WINDOW_UPDATE の増分値がイベントに含まれない**: 同コールバックの WINDOW_UPDATE イベントに RFC 9113 Section 6.9 (WINDOW_UPDATE) の Window Size Increment が含まれず、観測できない
- **受信トレーラ・1xx レスポンスが Headers イベントと区別できない**: `src/bindings/http2.cpp` と `src/bindings/http3.cpp` はトレーラ / 1xx を通常の HEADERS イベントとして積む。RFC 9114 Section 4.1 (HTTP Message Framing) の 1xx セマンティクスを扱うには不足
- **HTTP/3 の close_stream デフォルト error_code が H3_NO_ERROR でない**: `src/bindings/http3.cpp` のバインディングは `close_stream` のデフォルト error_code を 0 にしているが、RFC 9114 Section 8.1 の H3_NO_ERROR は 0x0100。HTTP/3 のエラーコード空間と不整合
- **HTTP/3 の goaway(id) の id 引数が無視される**: `src/bindings/http3.cpp` の `Http3Connection::goaway` は引数 id を受け取るが `nghttp3_conn_shutdown` には渡さず、GOAWAY ID は nghttp3 が内部算出する。死んだ引数になっている
- **HTTP/2 の data_source_read_callback が O(n²)**: `src/bindings/http2.cpp` の `Http2Connection::data_source_read_callback` は部分コピーごとに `front.data.erase` で全残データをシフトする。大バッファを max_frame_size 刻みで送出するとコピーコストが二乗で増える

## 設計方針

- PING イベントに opaque data を追加し、ACK フラグ付き PING も観測できるようにする (RTT 測定には ACK PING のエコー観測が必要)。`ping()` に 8 バイトの opaque data を渡せるようにする
- WINDOW_UPDATE イベントに増分値を追加する
- トレーラ・1xx を区別できるイベント種別または識別情報を追加する
- `close_stream` のデフォルト error_code を H3_NO_ERROR (0x0100) に変更する (後方互換の観点からデフォルト値変更の影響を確認する)
- `goaway` の id 引数を削除する。nghttp3 には GOAWAY ID を指定する API がなく (`nghttp3_conn_shutdown` は ID を内部算出する)、「意味のあるものにする」には nghttp3 の改修が必要なため対象外とする (公開 API の破壊的変更は CODEBASE.md の方針に従う)
- `data_source_read_callback` のバッファ管理をオフセット方式に変えて O(n²) を解消する

## 完了条件

- PING: 受信 PING イベントが 8 バイトの opaque data を持ち、ACK フラグ付き PING もイベントとして観測できる。`ping()` に渡した opaque data がピア側で観測され、ピアの ACK PING が同じ opaque data で観測される (対応分のテストが追加される)
- WINDOW_UPDATE: イベントが Window Size Increment を保持する (対応分のテストが追加される)
- トレーラ・1xx: 受信トレーラと 1xx が Headers イベントから区別でき、最終レスポンスとも区別できる (対応分のテストが追加される)
- `close_stream`: デフォルト error_code が H3_NO_ERROR (0x0100) となり、省略時の STREAM_END イベントの error_code が 0x0100 で観測される (対応分のテストが追加される)
- `goaway`: id 引数が削除され、`src/webtransport/http3/__init__.pyi` と既存の呼び出し側 (テスト) が更新される (対応分のテストが追加される)
- `data_source_read_callback`: オフセット方式になり、大きな送信バッファを max_frame_size 刻みで送出しても O(n²) のコピーが発生しない (対応分のテストが追加される)
- 全テストが通る

## 解決方法

polish-issue-deep で 6 項目を 5 件の独立した issue に分離した。実装は分離先で行う。本 issue は分離済みとして closed にする。PING と WINDOW_UPDATE は同一構造体・同一関数の隣接箇所のため 1 件に統合した。

- PING・WINDOW_UPDATE 項目 (観測性追加): `issues/0139-add-http2-event-fields.md` に移管
- トレーラ・1xx 項目 (観測性追加): `issues/0140-add-http-trailers-informational.md` に移管
- close_stream 項目 (デフォルト値変更): `issues/0141-change-close-stream-default-error.md` に移管
- goaway 項目 (死んだ引数の削除): `issues/0142-remove-goaway-dead-id.md` に移管
- O(n²) 項目 (性能改善): `issues/0143-perf-data-source-offset.md` に移管
