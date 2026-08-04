# ngtcp2 のストリーム・接続制御 API を公開する

- Created: 2026-08-04
- Completed: YYYY-MM-DD
- Branch: feature/add-quic-stream-control
- Polished: {YYYY-MM-DD}

## 目的

QUIC のストリーム上限確認・keep-alive・鍵更新・フロー制御の動的拡張を Python から行えるようにする。長時間接続の維持 (NAT タイムアウト対策) とセキュリティ (鍵更新) は実運用で必要になる。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection` はストリーム上限や keep-alive を公開しておらず、ストリーム上限とフロー制御は `QuicConfig` の接続作成時の設定値に固定され、keep-alive は設定手段が無い
- 本家 ngtcp2 (webtransport ブランチ) の以下の API が未使用 (列挙した getter は 1 系が deprecated のため 2 系 (const ポインタ版) で列挙する)
  - `ngtcp2_conn_get_streams_bidi_left2`: 開設可能な残り双方向ストリーム数
  - `ngtcp2_conn_get_streams_uni_left2`: 開設可能な残り単方向ストリーム数
  - `ngtcp2_conn_set_keep_alive_timeout`: keep-alive タイムアウトの設定
  - `ngtcp2_conn_initiate_key_update`: 鍵更新の開始
  - `ngtcp2_conn_extend_max_offset`: コネクション全体のフロー制御拡張
  - `ngtcp2_conn_extend_max_stream_offset`: ストリームのフロー制御拡張
  - `ngtcp2_conn_extend_max_streams_bidi`: 双方向ストリーム上限の拡張
  - `ngtcp2_conn_extend_max_streams_uni`: 単方向ストリーム上限の拡張

## 設計方針

- `QuicConnection` にストリーム・接続制御メソッドを追加し、nanobind で公開する (Python 側は `webtransport.quic.Connection`)。変更対象は `src/bindings/quic.cpp` / `src/bindings/quic.h` (メソッド追加・nanobind バインディング) とテスト (`tests/test_e2e_quic.py` 等)。`src/webtransport/quic/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要
- ngtcp2 の deprecated API (1 系) は使わず、2 系 (const ポインタ版) を使用する (get_streams_bidi_left / get_streams_uni_left は 2 系。その他の 6 API は 2 系が存在しないため 1 系)
- keep-alive は `set_keep_alive_timeout(timeout_ns: int)` として、ナノ秒で受け取る (UINT64_MAX (2**64 - 1) で無効化。ngtcp2 は 0 も現状無効扱いだが予約値のため使用しない)
- int を返す API (`initiate_key_update` / `extend_max_stream_offset`) は成功で True / 失敗で False を返す (`extend_max_stream_offset` は存在しないストリーム ID に 0 (成功) を返す点と、ローカル単方向ストリーム ID には NGTCP2_ERR_INVALID_ARGUMENT (False) を返す点に注意)。void の API (`set_keep_alive_timeout` / `extend_max_offset` / `extend_max_streams_bidi` / `extend_max_streams_uni`) は失敗しないため戻り値なし (extend_max_offset は NGTCP2_MAX_VARINT 超、extend_max_streams_bidi / uni は NGTCP2_MAX_STREAMS 超で黙ってクランプされる)
- `initiate_key_update` は ngtcp2 内部に assert (state == NGTCP2_CS_POST_HANDSHAKE) があり、クライアント側はハンドシェイク完了後も最初の post-handshake 送信 (write) まで state が遷移しない (サーバー側は read パスで遷移済みのため write 不要)。そのため C++ 側でクライアントのみ「ハンドシェイク完了後の write 実行済み」をガード条件とし、不成立時は False を返す (Release ビルドでは NGTCP2_ERR_INVALID_STATE が返るが、Debug ビルドでは assert で abort するため)。クライアントの initiate 成功には Handshake Done 受信 (read パスで HANDSHAKE_CONFIRMED が立つ) と、その後の write (state 遷移 + 新鍵準備) の両方が必要。タイムスタンプ引数は既存の `get_timestamp_ns` を使用する
- フロー制御拡張は ngtcp2 が自動では行わない (ストリーム上限は明示 API でのみ増加する。データフロー制御も通常の受信経路では再開放されず、現行バインディングの `recv_stream_data_cb` は受信データのフロー制御再開放を行っていない)。本 issue は extend API の公開を対象とし、自動拡張の実装 (受信データの再開放) は別 issue として扱う
- Python 側の公開名は 0014 / 0015 と同じく ngtcp2 の API 名から `get_` / `set_` を除いた形とする (例: `streams_bidi_left` / `streams_uni_left` プロパティ、`keep_alive_timeout(timeout_ns)` / `initiate_key_update()` / `extend_max_offset(datalen)` / `extend_max_stream_offset(stream_id, datalen)` / `extend_max_streams_bidi(n)` / `extend_max_streams_uni(n)` メソッド)。引数を取らない getter はプロパティ、その他はメソッドとして公開する
- コネクションが閉じている場合は、getter は `None` を返し (0014 と同じパターン)、setter / mutator は no-op とする (int を返す mutator は False を返す。既存メソッドと同じパターン)
- `get_streams_bidi_left` / `get_streams_uni_left` はハンドシェイク前は値が保証されない (クライアントは remote transport params 受信前は 0 を返す。サーバーは最初の receive 後にピアの広告値が反映される。0-RTT 時は古いセッションの広告値が入る)
- 0014 (接続統計) / 0015 (接続状態) も同じ `QuicConnection` を変更対象とするため、実装順序によるマージの競合に注意する (公開 API は互いに素)

## 完了条件

- Python から残り開設可能な双方向 / 単方向ストリーム数が取得できる (ハンドシェイク後にピアの広告値が反映される。ストリームを開くと残数が減る)
- Python から keep-alive を設定・無効化できる (ハンドシェイク完了後・パケット送受信後に `get_timeout` に反映される。keep-alive が他のタイマーより早く切れる場合)
- Python から鍵更新を開始できる (ハンドシェイク確認前や新鍵の準備が完了していない場合は失敗する場合がある。3*PTO の制約は 2 回目以降の鍵更新に適用される)
- Python からフロー制御とストリーム上限を拡張できる (効果はピア側で確認する: extend_max_offset はピア側の 0014 の `max_data_left` の増加、extend_max_stream_offset はピア側の 0014 の `max_stream_data_left` の増加、extend_max_streams_bidi / uni はピア側の本 issue の `streams_bidi_left` の増加。拡張した側が `send()` を呼ぶとフレームが送出され、ピアが受信した時点で反映される。0014 の残量 API に依存する確認は 0014 の実装後に行う)
- モックなしのテストで、各 API が動作することを確認する
