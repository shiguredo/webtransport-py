# ngtcp2 のストリーム・接続制御 API を公開する

- Created: 2026-08-04
- Completed: 2026-08-07
- Branch: feature/add-quic-stream-control
- Polished: 2026-08-07

## 目的

QUIC のストリーム上限確認・keep-alive・鍵更新・フロー制御の動的拡張を Python から行えるようにする。長時間接続の維持 (NAT タイムアウト対策。RFC 9000 Section 10.1.2 の idle timeout 延命) とセキュリティ (鍵更新。RFC 9001 Section 6) は実運用で必要になる。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection` はストリーム上限や keep-alive を公開しておらず、ストリーム上限とフロー制御は `QuicConfig` の接続作成時の設定値に固定され、keep-alive は設定手段が無い
- 本家 ngtcp2 (webtransport ブランチ) の以下の API が未使用 (getter は 2 系 (const ポインタ版) で列挙する)
  - `ngtcp2_conn_get_streams_bidi_left2`: 開設可能な残り双方向ストリーム数
  - `ngtcp2_conn_get_streams_uni_left2`: 開設可能な残り単方向ストリーム数
  - `ngtcp2_conn_set_keep_alive_timeout`: keep-alive タイムアウトの設定
  - `ngtcp2_conn_initiate_key_update`: 鍵更新の開始
  - `ngtcp2_conn_extend_max_offset`: コネクション全体のフロー制御拡張
  - `ngtcp2_conn_extend_max_stream_offset`: ストリームのフロー制御拡張
  - `ngtcp2_conn_extend_max_streams_bidi`: 双方向ストリーム上限の拡張
  - `ngtcp2_conn_extend_max_streams_uni`: 単方向ストリーム上限の拡張

## 設計方針

- `QuicConnection` にストリーム・接続制御メソッドを追加し、nanobind で公開する (Python 側は `webtransport.quic.Connection`)。変更対象は `src/bindings/quic.cpp` / `src/bindings/quic.h` (メソッド追加・nanobind バインディング) とテスト (0014 / 0015 と同じ Sans-IO 実通信構成の新規ファイル `tests/test_quic_stream_control.py`)。`src/webtransport/quic/__init__.pyi` はビルド生成物 (nanobind stub) のため更新不要
- ngtcp2 の deprecated API (1 系) は使わず、2 系 (const ポインタ版) を使用する (getter 2 つ (`get_streams_bidi_left2` / `get_streams_uni_left2`) は 1 系が deprecated のため 2 系。その他の 6 API は 2 系が存在しないため 1 系)
- keep-alive は `keep_alive_timeout(timeout_ns: int)` として、ナノ秒で受け取る (UINT64_MAX (2**64 - 1) で無効化。ngtcp2 は 0 も現状無効扱いだが、将来拡張用の予約値のため使用しない)
- int を返す API (`initiate_key_update` / `extend_max_stream_offset`) は成功で True / 失敗で False を返す (`extend_max_stream_offset` は存在しないストリーム ID (ローカル単方向を除く) に 0 (成功) を返す点と、ローカル単方向ストリーム ID (存在の有無を問わず、負値の単方向 ID も含む。ngtcp2 は存在判定より先にローカル単方向判定を行う) には NGTCP2_ERR_INVALID_ARGUMENT (False) を返す点に注意。NOMEM はモック禁止のためテスト不能)。void の API (`set_keep_alive_timeout` / `extend_max_offset` / `extend_max_streams_bidi` / `extend_max_streams_uni`) は失敗しないため戻り値なし (extend_max_offset / extend_max_stream_offset は NGTCP2_MAX_VARINT 超、extend_max_streams_bidi / uni は NGTCP2_MAX_STREAMS 超で黙ってクランプされる)
- `initiate_key_update` は ngtcp2 内部に assert (state == NGTCP2_CS_POST_HANDSHAKE) があり、クライアント側はハンドシェイク完了後も最初の post-handshake 送信 (write) まで state が遷移しない (サーバー側は read パスで遷移済みのため write 不要)。クライアントの initiate 成功には Handshake Done 受信 (read パスで HANDSHAKE_CONFIRMED が立つ。RFC 9001 Section 4.1.2 のクライアント側確認経路) と、その後の write (state 遷移 + 新鍵準備) の両方が必要。ハンドシェイク完了前の呼び出しは Debug ビルドでは assert で abort するため (Release ビルドでは assert が無効のためハンドシェイク未確認 (HANDSHAKE_CONFIRMED) または新鍵未準備により NGTCP2_ERR_INVALID_STATE が返り False になる)、C++ 側に両側ガードを設けて不成立時は False を返す: サーバーは「ハンドシェイク完了済み」のみで足りる (read パスで state 遷移が完了するため。新鍵準備は遷移後の post-handshake read / write で行われる)。クライアントはさらに「ハンドシェイク完了後の write 実行済み」を追加条件とする。ガードは C++ 側の新規フラグ (ハンドシェイク完了後の write 実行記録) で実装し、ハンドシェイク完了後の send() が 1RTT パケット (short header) を書き出せた場合に立てる (「ハンドシェイク完了」は既存の handshake_completed_ (TLS ハンドシェイク完了) を指し、Handshake Done 受信とは異なる。ペーシング / cwnd 制約時は ngtcp2 がハンドシェイク ACK のみを書き出して state 遷移せずに返ることがあるため、単にパケットを書き出せただけでは不十分。ハンドシェイク完了後の 1RTT パケットの書き出しは state 遷移と新鍵準備の後にしか起こらない (0-RTT 早期データはハンドシェイク完了前に 1RTT パケットとして書き出されるため対象外。フラグはハンドシェイク完了後しか立てない)。書き出せなかった場合は立てず、assert 回避のため安全側に倒す)。ガード通過後も Handshake Done 未受信 (HANDSHAKE_CONFIRMED 未成立) なら False が返る。新規メンバー追加に伴い、move コンストラクタ / move 代入演算子 (全メンバー明示コピー) にも反映する。タイムスタンプ引数は既存の `get_timestamp_ns` を使用する
- フロー制御拡張は ngtcp2 が自動では行わない (ストリーム上限は明示 API でのみ増加する (ngtcp2 の API ドキュメントには stream_open コールバック未発火のまま閉じられたストリームの自動増加の例外が記載されている)。データフロー制御も通常の受信経路では再開放されず、現行バインディングの `recv_stream_data_cb` は受信データのフロー制御再開放を行っていない。ngtcp2 の自動再開放は読み取り側シャットダウン (STOP_SENDING) 後の受信データ破棄と RESET_STREAM 完了時のみ)。本 issue は extend API の公開を対象とし、自動拡張の実装 (受信データの再開放) は別 issue として扱う
- Python 側の公開名は 0014 / 0015 の先例に合わせ、ngtcp2 の API 名から `get_` / `set_` を除いた形とする (0014 / 0015 は getter のみのため `get_` 除去のみだったが、本 issue は `set_keep_alive_timeout` を含むため `set_` も同様に除く。例: `streams_bidi_left` / `streams_uni_left` プロパティ、`keep_alive_timeout(timeout_ns)` / `initiate_key_update()` / `extend_max_offset(datalen)` / `extend_max_stream_offset(stream_id, datalen)` / `extend_max_streams_bidi(n)` / `extend_max_streams_uni(n)` メソッド)。引数を取らない getter はプロパティ、その他はメソッドとして公開する
- コネクションが閉じている場合は、getter は `None` を返し (0014 と同じパターン)、setter / mutator は no-op とする (int を返す mutator は False を返す。既存メソッドと同じパターン)
- `get_streams_bidi_left2` / `get_streams_uni_left2` はハンドシェイク前は値が保証されない (クライアントは remote transport params 受信前は 0 を返す。サーバーは最初の receive 後にピアの広告値が反映される。0-RTT 時は古いセッションの広告値が入る)。この前提は ngtcp2 の初期値 (max_streams が 0) に依存するため、C++ 側コメントに根拠を残す
- 0025 (RESET_STREAM_AT) / 0031 (CONNECTION_CLOSE の再送) も同じ `src/bindings/quic.cpp` を変更対象とするため、実装順序によるマージの競合に注意する

## 完了条件

- Python から残り開設可能な双方向 / 単方向ストリーム数が取得できる (ハンドシェイク後にピアの広告値が反映される。ストリームを開くと残数が減り、閉じても戻らない (RFC 9000 Section 19.11 の累積制限))
- Python から keep-alive を設定・無効化できる (ハンドシェイク完了後、パケット送受信で基準時刻が更新され、`get_timeout` に keep-alive の期限が反映される (keep-alive のタイムアウトをアイドルタイムアウト (既定 30 秒) より小さく設定し、未 ACK パケットが無い静穏状態で、keep-alive が他のタイマーより早く切れる場合)。期限超過後の handle_timeout → send() (書くべきフレームが無いアイドル状態) で PING が送出され、ピア側の `ping_recv` (0014) の増加で確認できる。UINT64_MAX で無効化できる)
- Python から鍵更新を開始できる (成功はクライアント (Handshake Done 受信 + 最初の post-handshake write 後) とサーバー (ハンドシェイク完了後の post-handshake read / write を 1 回経た後。新鍵準備が行われる) の両方で確認する。ハンドシェイク完了前はクライアント・サーバーとも False を返し、クライアントはさらに write 前も False を返す。鍵更新確認前の連続呼び出しは 2 回目が False (RFC 9001 Section 6.1 の MUST。ngtcp2 は鍵更新未確認フラグで実装)。3*PTO の制約 (RFC 9001 Section 6.5 の SHOULD。ngtcp2 はハード制約として実装) は 2 回目以降の鍵更新に適用される。3*PTO 経過後の再成功は ACK 交換と実時間経過に依存するため検証対象外とし、連続呼び出しの False のみを検証する)
- Python からフロー制御とストリーム上限を拡張できる (効果はピア側で確認する: extend_max_offset はピア側の 0014 の `max_data_left` の増加、extend_max_stream_offset はピア側の 0014 の `max_stream_data_left` の増加、extend_max_streams_bidi はピア側の本 issue の `streams_bidi_left` の増加、extend_max_streams_uni はピア側の本 issue の `streams_uni_left` の増加。フレームの送出条件に注意する: MAX_DATA / MAX_STREAM_DATA は ngtcp2 の流量制御により、未送出の拡張量が window/4 を超えた場合にのみ送出される (既定設定ではコネクション 256 KiB 超 / ストリーム 64 KiB 超。閾値未満の拡張は send() しても送出されない)。MAX_STREAMS は未送出の拡張があれば send() で送出される。テストでは閾値を考慮した拡張量で、ピアが受信した時点で反映されることを確認する。確認に使う 0014 の残量 API は実装済み)
- モックなしのテストで、各 API が動作することを確認する (コネクションが閉じた後は getter が `None`、setter / mutator が no-op / False になるガード経路も確認する)

## 解決方法

- `src/bindings/quic.cpp` / `src/bindings/quic.h` の `QuicConnection` にストリーム・接続制御 API 8 本を追加した: プロパティ `streams_bidi_left` / `streams_uni_left` (ngtcp2 の 2 系 API `get_streams_bidi_left2` / `get_streams_uni_left2`) と、メソッド `keep_alive_timeout` / `initiate_key_update` / `extend_max_offset` / `extend_max_stream_offset` / `extend_max_streams_bidi` / `extend_max_streams_uni`
- `initiate_key_update` は ngtcp2 内部の assert (state == NGTCP2_CS_POST_HANDSHAKE) 回避のため両側ガードを設けた: サーバーはハンドシェイク完了のみ、クライアントはハンドシェイク完了 + post-handshake の 1RTT パケット書き出し (新規メンバー `post_handshake_write_done_`) を条件とする。1RTT パケットの検出は、RFC 9000 Section 12.2 のコアレッシング (1 データグラムに複数パケット) を考慮し、QUIC パケットヘッダをパースして short header パケットの有無で判定する (`contains_short_header_packet`)。move コンストラクタ / move 代入演算子にも新規メンバーを反映した
- 設計方針の「0-RTT 早期データはハンドシェイク完了前に 1RTT パケットとして書き出される」は誤りで、0-RTT パケットは long header で書き出される (RFC 9000 Section 17.2.3)。実装は long header 判定で 0-RTT を対象外にできるため挙動に影響しない
- テストは `tests/test_quic_stream_control.py` に 9 件を追加した (0014 / 0015 と同じ Sans-IO 実通信構成・モックなし): ストリーム残数 (ハンドシェイク前 0 / 広告値反映 / 開設で減少 / 累積制限)、keep-alive (get_timeout 反映 / PING 送出 / ピアの ping_recv 増加 / UINT64_MAX 無効化)、鍵更新 (ハンドシェイク前 False / write 前 False / 成功 / 連続 2 回目 False)、フロー制御拡張 (window/4 の送出閾値 / ピア側残量の増加 / ローカル単方向 False)、閉じた後のガード (None / no-op / False)
