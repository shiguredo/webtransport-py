# HTTP/2 の data_source_read_callback の O(n²) コピーを解消する

- Created: 2026-09-03
- Completed: 2026-09-13
- Branch: feature/refactor-data-source-offset
- Polished: {YYYY-MM-DD}

## 目的

`Http2Connection::data_source_read_callback` の二乗コピーコストをオフセット方式で解消し、大バッファ送出時の性能劣化をなくす。

## 現状

- **HTTP/2 の data_source_read_callback が O(n²)**: `src/bindings/http2.cpp` の `Http2Connection::data_source_read_callback` は部分コピーごとに `front.data.erase` で全残データをシフトする。大バッファを `max_frame_size` (16384) 刻みで送出するとコピーコストが二乗で増える
- `src/bindings/http3.h` の `StreamData` はオフセット方式済みであり、同型で対応できる。`src/bindings/webtransport_h2.cpp` の `H2Session::data_source_read_callback` の同型パターンは本 issue の対象外とする (必要なら別 perf issue 化する)

## 設計方針

- `src/bindings/http2.h` の `StreamData` にオフセットを持たせ、部分コピーでは残データをシフトせずオフセットを進める (h3 側と同型)
- 0129 と同一ファイル (`src/bindings/http2.cpp`) を変更するため、並行着手する場合は順序調整または rebase 前提とする

## 完了条件

- オフセット方式であること・データ完全性・部分送出時の先頭残量保持を検証する単体テストがある (タイミング測定ではなく white-box 観測とする)
- 既存の全テストが通る

## 解決方法

- `src/bindings/http2.h` の `StreamData` に `offset` (送信済みバイト数) を追加し、`http3.h` の `StreamData` と同型にした
- `Http2Connection::data_source_read_callback` から `front.data.erase` を削除し、部分送出では `offset` を進める方式に変更した。nghttp2 が要求する長さは 1 フレーム分 (`max_frame_size` 以下) に収まるため、`memcpy` のコピー量は常にフレームサイズ相当になり O(n²) が解消される
- 実装中に `Http2Connection::send_data` の `push_back({data, eof})` が集約初期化の宣言順で `offset` に `eof` の値を格納し、送出データの先頭 1 バイトが欠落するバグを発見した。指定初期化子 `{.data = data, .eof = eof}` に修正した (旧実装の `erase` 方式でも同じ欠落が起きており、`offset` 追加前から潜在していた)
- 白箱観測用に `_test_stream_buffer_count` / `_test_stream_buffer_remaining` / `_test_stream_buffer_offset` を追加した
- 追加した単体テスト: オフセット方式 (バッファ総バイト数が変わらず `offset` だけ進む)、100 KiB のデータ完全性、複数エントリのエントリ単位オフセット、空データ + `eof=True` の END_STREAM 送出、空データ + `eof=False` が送信待ちデータを破棄しないこと

## 関連 issue

- `issues/closed/0123-refactor-http-event-details.md` — 分離元 (O(n²) 項目を移管)
- `issues/0129-add-http2-bindings-test-force-close.md` — 同一ファイルを変更するため順序調整
