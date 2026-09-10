# WebTransport over HTTP/2 のピア駆動マップに上限を設ける

- Created: 2026-09-07
- Completed: 2026-09-11
- Branch: feature/fix-h2-received-map-bound
- Polished: 2026-09-09

## 目的

ピアが送る未知 stream_id ごとに `received_max_stream_data_by_id` / `received_stop_sending_stream_ids` のマップが無制限に増える。リモート発火のメモリ DoS 経路であり、issue 0158 から分離した。マップはセッション破棄まで保持する設計のため、初期値ではなく累積ストリーム予算に連動した上限とし、超過時はセッションを閉じずに無視する (閉鎖方式では切断 DoS に付け替わるため)。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_max_stream_data` は `received_max_stream_data_by_id[stream_id] = max_data` で任意 stream_id のエントリを作成する (上限なし)
- `H2Session::handle_wt_stop_sending` は `received_stop_sending_stream_ids.insert(stream_id)` で任意 stream_id のエントリを作成する (上限なし)
- 両マップはセッション破棄まで保持する設計であり、同時 open 数ではなく累積で増える
- 両マップに入る stream_id はピア起点に限らない。`is_receivable_flow_capsule` は bidi (Bit 1 = 0) を無条件で受理し、uni も自側送信専用 (自側 initiator) を受理するため、自側起点ストリームの ID もエントリになる (`tests/test_webtransport_h2_initiator_validation.py` の自側送信専用ストリームへの受理テストで確認できる)
- 上限に使える累積値は `max_streams_bidi_remote` / `max_streams_uni_remote` (ピア起点の解放ごとに +1 される累積広告値) と `streams_bidi_opened` / `streams_uni_opened` (自側が open するごとに +1 される累積本数) の 4 つで、いずれも減少しない

## 設計方針

- 各マップ / 集合ごとに、上限をピア起点と自側起点の両方・bidi と uni の両方を含む累積ストリーム予算 `max_streams_bidi_remote + max_streams_uni_remote + streams_bidi_opened + streams_uni_opened` に連動させる (両コンテナで 1 つのカウンタを共有すると、片方の追加でもう片方の正規エントリを落とすため、コンテナごとに判定する)。ピア起点は解放ごとに `max_streams_*_remote` が +1、自側起点は open ごとに `streams_*_opened` が +1 されるため、正規の churn に追従する。初期値固定ではなく `WT_MAX_STREAMS` 再送出による増加にも追従する
- 上限判定はマップ / 集合への新規 ID の追加のみを対象とする。既存エントリがある ID と上限内の新規 ID は従来どおり処理する。上限超過の新規 ID はマップ / 集合に追加せず無視し、セッションは閉じない
- 上限超過で保持しない新規 ID は、draft-15 Section 6.6 の減少値検出と Section 6.3 の二重受信検出の対象外になる (メモリ DoS 回避を優先する設計判断。既存エントリがある ID では検出を維持する)
- `handle_wt_max_stream_data` は上限到達後も、既存ストリームが存在すれば `max_stream_data_local` の更新と `flush_pending_sends` を実行する (クレジット更新を落とさない)。`handle_wt_stop_sending` は上限内の初回受信で `StopSending` イベントを発火する
- 正規の churn (累積ストリーム予算内で多数のストリームを逐次利用し各回広告を受ける長期セッション) が誤って打ち切られないことをテストで確認する
- コード変更対象は `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h2.h`。これに加えてテストと `CHANGES.md` の develop への FIX エントリを付ける

## 完了条件

- 未知 stream_id を 30 万個 (既定の累積ストリーム予算を大きく超える数) 投入してもセッションが生存し、その後の既存ストリームへの `WT_MAX_STREAM_DATA` が送信クレジットに反映され、上限内の未知 ID への `WT_STOP_SENDING` が `StopSending` イベントとして発火すること
- 上限到達後も新規エントリの追加のみが止まり、既存エントリの更新と上限内の新規 ID のイベント送出は維持されること
- 正規 churn (累積ストリーム予算内で多数のストリームを逐次利用) が打ち切られないこと
- `tests/` に未知 ID 大量投入テストと churn テストを追加すること
- `CHANGES.md` の develop に FIX エントリが追加されていること

## 解決方法

- `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` の受信系コンテナ (`received_max_stream_data_by_id` / `received_stop_sending_stream_ids`) に固定の安全弁 `kMaxReceivedMapEntries` (4096) を設け、上限超過の新規 stream_id を保持しないようにした
- 実在ストリームへのイベント通知とクレジット反映は上限に関係なく維持し、`WT_STOP_SENDING` の二重受信検出は `WtStreamInfo` のフラグで上限到達後も維持する
- `WT_MAX_STREAM_DATA` の減少検出は既存エントリと実在ストリームの `max_stream_data_local` フォールバックで維持する
- 実装中に判明した既知の制約: 上限超過の未作成 ID では、事前クレジット広告 (`WT_MAX_STREAM_DATA` の先行受信) と二重受信検出が対象外になる。メモリ有界化とのトレードオフであり、コードコメントに明記した
- 当初の設計方針 (累積ストリーム予算への連動) は、ピアのストリーム churn (解放済み ID の再作成) で予算が無制限に水増しされることがレビューで判明したため、固定上限に変更した
- `tests/test_webtransport_h2_received_map_bound.py` に 7 テスト (30 万 ID 大量投入・上限境界・実在ストリームの通知と二重検出維持・減少検出の維持と制約) を追加し、全 983 テストと WebKit ブラウザ E2E 8 件の通過を確認した
- 既存のテスト全 976 件が引き続き通過すること
