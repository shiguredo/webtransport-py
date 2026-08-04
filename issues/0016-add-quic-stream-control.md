# ngtcp2 のストリーム・接続制御 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-quic-stream-control
- Polished: {YYYY-MM-DD}

## 目的

QUIC のストリーム上限確認・keep-alive・鍵更新・フロー制御の動的拡張を Python から行えるようにする。長時間接続の維持 (NAT タイムアウト対策) とセキュリティ (鍵更新) は実運用で必要になる。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection` はストリーム上限や keep-alive を公開しておらず、設定は `QuicConfig` のビルド時値に固定されている
- 本家 ngtcp2 (webtransport ブランチ) の以下の API が未使用
  - `ngtcp2_conn_get_streams_bidi_left`: 開設可能な残り双方向ストリーム数
  - `ngtcp2_conn_get_streams_uni_left`: 開設可能な残り単方向ストリーム数
  - `ngtcp2_conn_set_keep_alive_timeout`: keep-alive タイムアウトの設定
  - `ngtcp2_conn_initiate_key_update`: 鍵更新の開始
  - `ngtcp2_conn_extend_max_offset`: コネクション全体のフロー制御拡張
  - `ngtcp2_conn_extend_max_stream_offset`: ストリームのフロー制御拡張
  - `ngtcp2_conn_extend_max_streams_bidi`: 双方向ストリーム上限の拡張
  - `ngtcp2_conn_extend_max_streams_uni`: 単方向ストリーム上限の拡張

## 設計方針

- `QuicConnection` にストリーム・接続制御メソッドを追加し、nanobind で公開する (Python 側は `webtransport.quic.Connection`)
- keep-alive は `set_keep_alive_timeout(timeout_ns: int)` として、ナノ秒で受け取る (0 で無効化)
- 鍵更新とフロー制御拡張は、失敗時に `False` / `None` を返す形で公開する
- フロー制御拡張は通常 ngtcp2 が自動で行うため、上級者向け API としてドキュメントで明示する
- `src/webtransport/quic/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要

## 完了条件

- Python から残り開設可能な双方向 / 単方向ストリーム数が取得できる
- Python から keep-alive を設定・無効化できる
- Python から鍵更新を開始できる
- Python からフロー制御とストリーム上限を拡張できる
- モックなしのテストで、各 API が動作することを確認する
