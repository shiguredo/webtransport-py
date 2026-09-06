# WebTransport over HTTP/2 の受信バッファとピア駆動のマップが無制限に増えるメモリ DoS 経路がある

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-unbounded-buffer-dos
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/2 のサーバーで、ピアが Length を巨大にした WT_STREAM カプセルを送るとペイロードが揃うまで `capsule_buffer` に無制限蓄積する。HTTP/2 レベルは nghttp2 が自動 WINDOW_UPDATE を送るため受信は止まらず、WT レベルの `max_data_remote` は capsule 完成後にしか検査されない。加えてピアが送る未知 stream_id ごとに `received_max_stream_data_by_id` / `received_stop_sending_stream_ids` のマップが増える。実験で 64 MiB / 30 万エントリ級のメモリ増加を確認しており、リモートから発火可能なメモリ DoS 経路。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::process_capsules` は Length が揃うまで `wt_session->capsule_buffer` に加算する (上限なし)
- `handle_wt_max_stream_data` は `received_max_stream_data_by_id[stream_id] = max_data` で任意 stream_id のエントリを作成 (上限なし)
- `handle_wt_stop_sending` は `received_stop_sending_stream_ids.insert(stream_id)` で任意 stream_id のエントリを作成 (上限なし)
- HTTP/2 レベルは nghttp2 が既定で自動 WINDOW_UPDATE を送る (`nghttp2_option_set_no_auto_window_update` 未使用)
- WT レベルの `max_data_remote` の検査は `handle_wt_stream` 内で capsule 完成後に行われる (Length が届いた時点では検査しない)
- `WtSessionInfo::streams` に `erase` 経路が無い (`webtransport_h2.cpp` を `erase` で grep して確認)
- 実験: Length = 2^30 の WT_STREAM ヘッダー + 64 MiB を 16 KiB DATA で注入 → プロセス RSS + 128 MiB、WINDOW_UPDATE 2048 回、エラーなし、セッション生存
- 実験: WT_MAX_STREAM_DATA を 30 万個送出 (ワイヤ 3.7 MiB) → RSS + 34 MiB、WT_STOP_SENDING 30 万個 → RSS + 125 MiB
- `receive` / `send_stream_data` / `send_datagram` / `send_data` の Python → C++ 経路 (`webtransport_h2.cpp` の nb::bytes → std::vector) は無制限にコピー (shiguredo-python 規約「入力サイズの上限を明示的に検査すること」違反)

## 設計方針

- カプセル長上限を H2SessionConfig に追加する (既定は妥当な値、例えば 1 MiB)。Length が上限を超えた WT_STREAM / WT_STREAM_FIN を受信した時点で WT_ERROR (プレースホルダ 0x52) でセッションを閉じる
- `received_max_stream_data_by_id` / `received_stop_sending_stream_ids` の上限を stream 数制限と同じ (`wt_initial_max_streams_*_remote`) に紐付ける。上限超過は WT_FLOW_CONTROL_ERROR
- 両ハーフ終端したストリームエントリを `WtSessionInfo::streams` から解放する (issue 0156 と協調)
- `nghttp2_option_set_no_auto_window_update` を有効にし、`nghttp2_session_consume` によるアプリ消費連動の背圧を実装する
- Python 境界 (`receive` / `send_stream_data` / `send_datagram` / `send_data`) の入力サイズ上限を検査する (shiguredo-python 規約)
- 受理前カプセルのバッファ上限 (issue 0157) と一貫させる (補充閾値 < バッファ上限)

## 完了条件

- Length = 2^30 のカプセルヘッダーを受信すると WT_ERROR でセッションが閉じ、RSS が増えないこと
- 未知 stream_id を 30 万個投入するとメモリ増加が有界であること
- 両ハーフ終端したストリームエントリが解放されること
- `tests/` に長大 capsule / 未知 ID マップの上限テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
