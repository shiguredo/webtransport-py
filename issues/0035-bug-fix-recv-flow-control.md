# QUIC の受信フロー制御が初期受信ウィンドウを超えて前進しない問題を修正する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/fix-recv-flow-control
- Polished: YYYY-MM-DD
- Reporter: @voluntas

## 目的

QUIC の受信フロー制御を前進させ、初期受信ウィンドウを超える大容量データ転送を止まらずに完了できるようにする。ngtcp2-py から webtransport-py への置き換えに必須の修正である。

## 現状

- `src/bindings/quic.cpp` の `recv_stream_data_cb` は受信データを `QuicEvent` に積むだけで、`ngtcp2_conn_extend_max_stream_offset()` と `ngtcp2_conn_extend_max_offset()` を呼んでいない
- 受信ウィンドウは接続作成時に広告した初期値 (QuicConfig の既定ではストリーム 256 KiB / コネクション 1 MiB) のまま増えないため、送信側は初期ウィンドウを超えて送れず、大容量転送は受信側のフロー制御ブロックで停止する
- sora-quic の ngtcp2-py テスト (100 KiB / 256 KiB / 512 KiB の echo) は webtransport-py の `Client` に置き換えると途中で止まる
- 既存 issue は「自動拡張の実装 (受信データの再開放) は別 issue として扱う」として分離済みであり、重複しない

## 設計方針

- ngtcp2-py の `recv_stream_data_cb` と同じ順序で再開放を行う: イベント push 後に `ngtcp2_conn_extend_max_stream_offset(conn, stream_id, datalen)` を呼び、返り値が異常ならその値を返す。正常なら `ngtcp2_conn_extend_max_offset(conn, datalen)` を呼び、最後に 0 を返す
- 再開放量は受信した `datalen` そのものとする (ngtcp2-py と同じ)

## 完了条件

- 受信データ量ぶんのストリーム・コネクション両方のフロー制御が前進する
- 初期受信ウィンドウ (256 KiB) を超える大容量 echo 転送が止まらず完了する (テストを追加する)
- 既存の全テストが通る

## 解決方法

(実装時に追記する)