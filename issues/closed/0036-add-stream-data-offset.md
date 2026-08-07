# STREAM_DATA イベントにストリームオフセットを追加する

- Created: 2026-08-07
- Completed: 2026-08-07
- Branch: feature/add-stream-data-offset
- Polished: 2026-08-07
- Reporter: @voluntas

## 目的

低レベル `Event` にストリームオフセットを公開し、ngtcp2-py との API 互換 (drop-in 置換) を実現する。ngtcp2-py は `Event.offset` を公開しており、置き換えに必要である。

## 現状

- `src/bindings/quic.h` の `QuicEvent` は `type` / `stream_id` / `data` / `fin` / `error_code` / `reason` のみで、ストリームオフセットを持たない
- `src/bindings/quic.cpp` の `recv_stream_data_cb` は ngtcp2 が渡す `offset` を捨てている
- ngtcp2.h の `ngtcp2_recv_stream_data` はデータを offset の非減少順・重複なしで渡すことを保証し、実装上は gap を含まない形で連続配送する。そのため本 API は reorder の再構成ではなく、受信データのストリーム上の絶対位置を公開する用途になる
- ngtcp2-py は `Event.offset` を公開している

## 設計方針

- `QuicEvent` に `uint64_t offset` を追加し、`recv_stream_data_cb` で ngtcp2 から渡された offset を設定する
- フィールドは `QuicEvent` の末尾 (reason の直後) にデフォルト値 0 で追加する。`push_event` の集約初期化で type / stream_id / data / fin / error_code / reason の 6 要素を渡している箇所 (12 箇所) は、末尾追加なら offset がデフォルト値初期化で 0 になりコード変更の必要がない
- nanobind バインディングに `.def_ro("offset", ...)` を追加する (ngtcp2-py と同じプロパティ名)。ビルド後に `src/webtransport/quic/__init__.pyi` が再生成されることを確認する (`make develop` が `_build/quic.pyi` から反映する。`src/webtransport/quic.pyi` は再生成対象外の残骸ファイルのため対象にしない)
- offset は STREAM_DATA イベントのみで有意味であり、他イベントでは 0 になる (stream_id が非 STREAM_DATA で -1 になるのと同様)。STREAM フレームの offset は変長整数であり、offset + data length の合計が 2^62-1 を超えない (RFC 9000 Section 16 / Section 19.8)
- 0037 (高レベル `Client` への `recv_stream_data` 追加) は ngtcp2 の連続配送保証により reorder 再構成を実装しない設計のため、offset は recv_stream_data の実装では使用しない
- 0035 (受信フロー制御の前進) も同じ `recv_stream_data_cb` を変更対象とするため、実装順序によるマージの競合に注意する

## 完了条件

- 低レベル `Event` から STREAM_DATA のストリームオフセットが取得できる
- STREAM_DATA イベントの offset が受信データの累積位置と一致することを検証するテストを追加する (データを複数チャンクに分けて送信し、各イベントの offset が累積位置と一致することを確認する。テストは初期受信ウィンドウ (256 KiB) 内のデータ量で行う)
- 既存の全テストが通る

## 解決方法

- `src/bindings/quic.h` の `QuicEvent` の末尾 (reason の直後) に `uint64_t offset = 0` を追加した。デフォルト値 0 の末尾追加のため、`push_event` の集約初期化 (12 箇所) は変更不要で、STREAM_DATA 以外のイベントでは offset が 0 になる
- `src/bindings/quic.cpp` の `recv_stream_data_cb` で ngtcp2 から渡された `offset` を `event.offset` に設定し、nanobind バインディングに `.def_ro("offset", ...)` を追加した (ngtcp2-py と同じプロパティ名)。ビルド後に `src/webtransport/quic/__init__.pyi` へ再生成されることを確認した
- テストは `tests/test_quic_stream_data_offset.py` に 1 件を追加した。データを 5 チャンクに分けて送信し、受信イベントの offset が「前のイベントの offset + データ長」と一致すること (累積位置の追跡) と、全チャンクが欠落なく届くことを確認する。ngtcp2 の契約は「offset の非減少順・重複なしで渡す」ことのみであり、連続配送は reorder buffer の挙動に依存する点を docstring に明記した
