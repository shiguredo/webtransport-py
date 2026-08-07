# QUIC の受信フロー制御が初期受信ウィンドウを超えて前進しない問題を修正する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/fix-recv-flow-control
- Polished: 2026-08-07
- Reporter: @voluntas

## 目的

QUIC の受信フロー制御を前進させ、初期受信ウィンドウを超える大容量データ転送を止まらずに完了できるようにする。ngtcp2-py から webtransport-py への置き換えに必須の修正である。

## 現状

- `src/bindings/quic.cpp` の `recv_stream_data_cb` は受信データを `QuicEvent` に積むだけで、`ngtcp2_conn_extend_max_stream_offset()` と `ngtcp2_conn_extend_max_offset()` を呼んでいない
- 受信ウィンドウは接続作成時に広告した初期値 (QuicConfig の既定ではストリーム 256 KiB / コネクション 1 MiB) のまま増えず、送信側は広告された上限を超えて送れない (RFC 9000 Section 4.1 の "Senders MUST NOT send data in excess of either limit")。そのため初期ウィンドウを超えるデータ転送は受信側のフロー制御ブロックで停止する
- sora-quic の ngtcp2-py テストには 100 KiB / 256 KiB / 512 KiB の echo があり、webtransport-py の `Client` に置き換えると 512 KiB (クライアントの受信ウィンドウ 256 KiB 超) が途中で止まる。100 KiB / 256 KiB は初期ウィンドウ内で完結する。ngtcp2-py の既定ウィンドウは 64 MB であり、sora-quic のテストは再開放なしでも通るため、停止の直接原因は webtransport-py の既定ウィンドウの小ささである
- 既存の 0016 (`ngtcp2` のストリーム・接続制御 API を公開する) は「本 issue は extend API の公開を対象とし、自動拡張の実装 (受信データの再開放) は別 issue として扱う」として再開放を本 issue に分離しており、重複しない

## 設計方針

- ngtcp2-py の `recv_stream_data_cb` と同じ順序で再開放を行う: イベント push 後に `ngtcp2_conn_extend_max_stream_offset(conn, stream_id, datalen)` を呼び、返り値が異常ならその値を返す。正常なら `ngtcp2_conn_extend_max_offset(conn, datalen)` を呼び、最後に 0 を返す。再開放量は受信した `datalen` そのものとする
- 受信データをイベントキューに積んだ直後に即時・全量再開放するため、アプリがデータを消費する前にウィンドウが戻り、受信フロー制御はメモリ保護として機能しなくなる。これは ngtcp2-py と同じ挙動であり、意図的な選択である (RFC 9000 Section 4.2 は再開放のタイミングを実装の判断に委ねる)
- 再開放の効果は ngtcp2 が MAX_STREAM_DATA / MAX_DATA フレームとして送出することでピアに伝わり、未送出の拡張量が window/4 を超えた場合にのみ送出される (0016 で確認済み)。256 KiB 超の echo 転送ではこの閾値を超えるため送出される
- 0036 (STREAM_DATA イベントへの offset 追加) も同じ `recv_stream_data_cb` を変更対象とするため、実装順序によるマージの競合に注意する

## 完了条件

- `recv_stream_data_cb` が受信したデータ量ぶんのストリーム・コネクション両方のフロー制御を再開放する (新規の Sans-IO 実通信テストで、ピア側のフロー制御残量 API (0014 の `max_stream_data_left` / `max_data_left`) の増加を確認する。MAX_STREAM_DATA / MAX_DATA は未送出の拡張量が window/4 を超えた場合にのみ送出されるため、閾値超のデータ量で転送する)
- 初期受信ウィンドウ (256 KiB) を超える大容量 echo 転送 (512 KiB 以上) が止まらず完了する (e2e の回帰テストを追加する。512 KiB はストリームレベルの再開放を検証し、コネクションレベルの再開放は完了条件 1 で検証する)
- 既存の全テストが通る

## 解決方法

(実装時に追記する)