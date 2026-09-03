# HTTP/2 の data_source_read_callback の O(n²) コピーを解消する

- Created: 2026-09-03
- Completed: {YYYY-MM-DD}
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

## 関連 issue

- `issues/closed/0123-refactor-http-event-details.md` — 分離元 (O(n²) 項目を移管)
- `issues/0129-add-http2-bindings-test-force-close.md` — 同一ファイルを変更するため順序調整
