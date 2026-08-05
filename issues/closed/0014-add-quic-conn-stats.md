# ngtcp2 の接続統計 API を公開する

- Created: 2026-08-04
- Completed: 2026-08-05
- Branch: feature/add-quic-conn-stats
- Polished: 2026-08-04

## 目的

QUIC コネクションのネットワーク品質 (RTT・輻輳ウィンドウ・フロー制御残量・送受信量など) を Python から取得できるようにし、アプリケーションが帯域推定・品質監視・輻輳制御の可視化を行えるようにする。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection` は ngtcp2 の統計系 API を一切公開していない
- 本家 ngtcp2 (webtransport ブランチ) の以下の API が未使用 (列挙した getter は 1 系が deprecated のため 2 系 (const ポインタ版) で列挙する)
  - `ngtcp2_conn_get_conn_info2`: `ngtcp2_conn_info` (V2。latest_rtt / min_rtt / smoothed_rtt / rttvar / cwnd / ssthresh / bytes_in_flight / pkt_sent / bytes_sent / pkt_recv / bytes_recv / pkt_lost / bytes_lost / ping_recv / pkt_discarded) を取得できる
  - `ngtcp2_conn_get_pto2`: PTO (再送タイムアウト)
  - `ngtcp2_conn_get_cwnd_left2`: 輻輳ウィンドウ残量
  - `ngtcp2_conn_get_max_data_left2`: コネクション全体のフロー制御残量
  - `ngtcp2_conn_get_max_stream_data_left2`: ストリームごとのフロー制御残量
  - `ngtcp2_conn_get_stream_loss_count2`: ストリームの損失パケット数
  - `ngtcp2_conn_get_send_quantum2`: 送信クォンタム
  - `ngtcp2_conn_get_path_max_tx_udp_payload_size2`: 現在パスの最大 UDP ペイロードサイズ
- Python からは `is_established()` / `is_handshake_completed()` 程度の状態確認しかできず、接続品質を測る手段が無い

## 設計方針

- `QuicConnection` に統計取得メソッドを追加し、nanobind で公開する (Python 側は `webtransport.quic.Connection`)。変更対象は `src/bindings/quic.cpp` / `src/bindings/quic.h` (メソッド追加・nanobind バインディング) とテスト (`tests/test_debug_quic.py` 等)。`src/webtransport/quic/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要
- ngtcp2 の deprecated API (1 系) は使わず、2 系 (const ポインタ版) を使用する
- `ngtcp2_conn_info` は個別フィールドのプロパティで公開する (nanobind の `def_prop_ro`。既存の公開パターンに整合)。RTT 系 (latest_rtt / min_rtt / smoothed_rtt / rttvar) と PTO のみ単位はナノ秒のままとし、Python 側で変換できるようにする (その他のフィールドはバイト・個数)
- ストリーム ID を引数に取る API (`get_max_stream_data_left2` / `get_stream_loss_count2`) はストリーム ID 引数のメソッドとして公開し、ストリーム ID を取らない API はプロパティとして公開する。Python 側の公開名は ngtcp2 の API 名から `get_` を除いた形とする (例: `max_data_left` プロパティ、`stream_loss_count(stream_id)` メソッド、conn_info の各フィールドは `latest_rtt` 等のプロパティ)
- 取得はスナップショットであり、コネクションが閉じている場合は `None` を返す (閉鎖時の None 化のみ既存の `get_timeout_ns` と同じパターン)。ハンドシェイク前は ngtcp2 が初期値を返すため `None` にはならない (ストリーム系は存在しないストリームに 0 を返す。`UINT64_MAX` 等の無意味値は変換せずそのまま返す)
- `ngtcp2_conn_get_timestamp` (内部タイムスタンプ) はアプリケーション用途が無いため公開しない
- 0015 (接続状態) / 0016 (ストリーム制御) も同じ `QuicConnection` を変更対象とするため、実装順序によるマージの競合に注意する (公開 API は互いに素)

## 完了条件

- Python から RTT (latest / min / smoothed / rttvar)・cwnd・ssthresh・bytes_in_flight・送受信パケット数・送受信バイト数・損失パケット数・損失バイト数・PING 受信数・破棄パケット数が取得できる (`ngtcp2_conn_info` の全フィールド)
- Python から PTO・輻輳ウィンドウ残量・フロー制御残量 (コネクション / ストリーム)・ストリームの損失パケット数・送信クォンタム・最大ペイロードサイズが取得できる
- モックなしのテストで、ハンドシェイク後の値が取得できること、ハンドシェイク前は `None` にならず初期値 (`UINT64_MAX` 等) がそのまま返ること、存在しないストリーム ID には 0 が返ること、コネクションが閉じている場合は `None` を返すことを確認する

## 解決方法

`src/bindings/quic.cpp` / `quic.h` の `QuicConnection` に接続統計取得 API を追加し、nanobind で公開した (Python 側は `webtransport.quic.Connection`)。

- `ngtcp2_conn_info` (V2) の全 15 フィールドを個別プロパティで公開する (`latest_rtt` / `min_rtt` / `smoothed_rtt` / `rttvar` / `cwnd` / `ssthresh` / `bytes_in_flight` / `pkt_sent` / `bytes_sent` / `pkt_recv` / `bytes_recv` / `pkt_lost` / `bytes_lost` / `ping_recv` / `pkt_discarded`)。RTT 系 4 フィールドはナノ秒のまま
- `pto` / `cwnd_left` / `max_data_left` / `send_quantum` / `path_max_tx_udp_payload_size` をプロパティで公開し、ストリーム ID を引数に取る `max_stream_data_left(stream_id)` / `stream_loss_count(stream_id)` をメソッドで公開する
- deprecated の 1 系ではなく 2 系 (const ポインタ版) のみを使用する
- コネクションが無いか閉じている場合のみ `None` を返し、ハンドシェイク前の初期値 (`UINT64_MAX` 等) は変換せずそのまま返す (ストリーム系は存在しないストリームに 0)
- `ngtcp2_conn_get_timestamp` はアプリケーション用途が無いため公開しない

テストは `tests/test_quic_conn_stats.py` に追加した。`test_conn_stats_before_handshake` (ハンドシェイク前の初期値)、`test_conn_stats_after_handshake` (ハンドシェイク後の値取得とデータ送信によるフロー制御残量の減少・送受信カウンタの増加)、`test_conn_stats_nonexistent_stream` (存在しない・負・巨大なストリーム ID への 0 返却)、`test_conn_stats_after_close` (クローズ後の None) の 4 テストで構成する。テストは `tests/test_quic_error_handling.py` と同じモックなしの Sans-IO 実通信構成。
