# ngtcp2 の接続統計 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-quic-conn-stats
- Polished: {YYYY-MM-DD}

## 目的

QUIC コネクションのネットワーク品質 (RTT・輻輳ウィンドウ・フロー制御残量・送受信量など) を Python から取得できるようにし、アプリケーションが帯域推定・品質監視・輻輳制御の可視化を行えるようにする。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection` は ngtcp2 の統計系 API を一切公開していない
- 本家 ngtcp2 (webtransport ブランチ) の以下の API が未使用
  - `ngtcp2_conn_get_conn_info2`: `ngtcp2_conn_info` (latest_rtt / min_rtt / smoothed_rtt / rttvar / cwnd / ssthresh / bytes_in_flight / pkt_sent / bytes_sent / pkt_recv / bytes_recv) を返す
  - `ngtcp2_conn_get_pto`: PTO (再送タイムアウト)
  - `ngtcp2_conn_get_cwnd_left`: 輻輳ウィンドウ残量
  - `ngtcp2_conn_get_max_data_left`: コネクション全体のフロー制御残量
  - `ngtcp2_conn_get_max_stream_data_left`: ストリームごとのフロー制御残量
  - `ngtcp2_conn_get_stream_loss_count`: ストリームの損失バイト数
  - `ngtcp2_conn_get_send_quantum`: 送信クォンタム
  - `ngtcp2_conn_get_path_max_tx_udp_payload_size`: 現在パスの最大 UDP ペイロードサイズ
  - `ngtcp2_conn_get_timestamp`: 内部タイムスタンプ
- Python からは `is_established()` / `is_handshake_completed()` 程度の状態確認しかできず、接続品質を測る手段が無い

## 設計方針

- `QuicConnection` に統計取得メソッドを追加し、nanobind で公開する (Python 側は `webtransport.quic.Connection`)
- `ngtcp2_conn_info` は個別フィールドのプロパティまたは辞書で公開する。単位はナノ秒のままとし、Python 側で変換できるようにする
- ストリーム ID を引数に取る API (`get_max_stream_data_left` / `get_stream_loss_count`) はストリーム ID 引数のメソッドとして公開する
- 取得はスナップショットであり、コネクションが閉じている場合やハンドシェイク前に取得できない値は `None` を返す
- `src/webtransport/quic/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要

## 完了条件

- Python から RTT (latest / min / smoothed / rttvar)・cwnd・ssthresh・bytes_in_flight・送受信パケット数・送受信バイト数が取得できる
- Python から PTO・輻輳ウィンドウ残量・フロー制御残量 (コネクション / ストリーム)・損失バイト数・送信クォンタム・最大ペイロードサイズが取得できる
- モックなしのテストで、ハンドシェイク後の値が取得できることを確認する
