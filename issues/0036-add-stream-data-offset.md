# STREAM_DATA イベントにストリームオフセットを追加する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-stream-data-offset
- Polished: YYYY-MM-DD
- Reporter: @voluntas

## 目的

低レベル `Event` にストリームオフセットを公開し、高レベル `Client` の `recv_stream_data` で順序逆転 (reorder) を再構成できるようにする。ngtcp2-py 置き換えで必要な前提 API である。

## 現状

- `src/bindings/quic.h` の `QuicEvent` は `stream_id` / `data` / `fin` / `error_code` / `reason` のみで、ストリームオフセットを持たない
- `src/bindings/quic.cpp` の `recv_stream_data_cb` は ngtcp2 が渡す `offset` を捨てており、Python 側からは受信データの並び替え判定ができない
- ngtcp2-py は `Event.offset` を公開し、Python 側で `_ingest_stream_data` を使って reorder を再構成している

## 設計方針

- `QuicEvent` に `uint64_t offset` を追加し、`recv_stream_data_cb` で ngtcp2 から渡された offset を設定する
- nanobind バインディングに `.def_ro("offset", ...)` を追加する (ngtcp2-py と同じプロパティ名)
- `src/webtransport/quic.pyi` / `src/webtransport/quic/__init__.pyi` はビルド生成物 (nanobind が生成し `make develop` で反映) のため、ビルド後に再生成を確認する

## 完了条件

- 低レベル `Event` から STREAM_DATA のストリームオフセットが取得できる
- 既存の全テストが通る

## 解決方法

(実装時に追記する)