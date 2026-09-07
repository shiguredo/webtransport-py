# WebTransport over HTTP/2 のピア駆動マップに上限を設ける

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-received-map-bound
- Polished: {YYYY-MM-DD}

## 目的

ピアが送る未知 stream_id ごとに `received_max_stream_data_by_id` / `received_stop_sending_stream_ids` のマップが無制限に増える。リモート発火のメモリ DoS 経路であり、issue 0158 から分離した。マップはセッション破棄まで保持する設計のため、初期値ではなく現在広告値に連動した上限とし、超過時はセッションを閉じずに無視する (閉鎖方式では切断 DoS に付け替わるため)。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_max_stream_data` は `received_max_stream_data_by_id[stream_id] = max_data` で任意 stream_id のエントリを作成する (上限なし)
- `H2Session::handle_wt_stop_sending` は `received_stop_sending_stream_ids.insert(stream_id)` で任意 stream_id のエントリを作成する (上限なし)
- 両マップはセッション破棄まで保持する設計であり、同時 open 数ではなく累積で増える

## 設計方針

- 両マップの上限を現在広告中のストリーム数制限に連動させる (初期値固定ではなく、`WT_MAX_STREAMS` 再送出による増加に追従する)
- 上限超過分のカプセルは無視して破棄し、セッションは閉じない
- 正規の churn (逐次に多数のストリームを使い各回広告を受ける長期セッション) が誤って打ち切られないことをテストで確認する
- 変更対象は `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h2.h` のみとする

## 完了条件

- 未知 stream_id を 30 万個投入してもメモリ増加が有界であること
- 正規 churn が打ち切られないこと
- `tests/` に未知 ID 大量投入テストを追加すること
- 既存のテスト全 834 件が引き続き通過すること
