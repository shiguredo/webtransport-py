# 高レベル QUIC クライアントに recv_stream_data を追加する

- Created: 2026-08-07
- Completed: YYYY-MM-DD
- Branch: feature/add-recv-stream-data
- Polished: YYYY-MM-DD
- Reporter: @voluntas

## 目的

高レベル `Client` に ngtcp2-py 互換の `recv_stream_data` を追加し、request-response 型テストを webtransport-py に置き換えられるようにする。テストの大半がこの API を使うため最も使用頻度が高い機能である。

## 現状

- webtransport-py の高レベル `Client` にはストリームデータを FIN まで待って受信する API が無く、`on_stream_data` コールバック型のみ提供されている
- ngtcp2-py は `recv_stream_data(stream_id, timeout=10.0, *, overall_timeout=None) -> tuple[bytes, bool]` を提供しており、sora-quic の各テストが `data, fin = await client.recv_stream_data(stream_id, timeout=...)` の形で使用する
- 低レベル binding は受信データのフロー制御前進をせず、`Event` に offset が無いため、この API を実装するには両方の前提が必要 (別 issue で対応する)

## 設計方針

- ngtcp2-py の `client.py` の実装を移植する
- ストリームごとの受信状態を管理し、`event.offset` と `event.fin` から reorder を再構成する (gap 検出 / 重複セグメントのマージ / final size の整合性検証 / 完了判定。ngtcp2-py の `_StreamReceiveState` に相当)
- 2 段構えのタイムアウト: 進捗があるたびに延びる idle deadline (`timeout`) と、進捗に関係なく動かない absolute deadline (`overall_timeout`。None なら `max(timeout * 6, 30)` を使う)
- FIN を受信したら `(bytes, bool)` を返し、どちらかの期限に達したら受信済みバイト数・FIN 状態・timeout 値を含むメッセージで `TimeoutError` を raise する
- 既存の `on_stream_data` コールバックとの併用は維持する (コールバックは従来どおり発火させる)

## 完了条件

- `recv_stream_data` が FIN までデータを受信し `(data, fin)` を返す
- 順序逆転したセグメントを再構成して全データを返す
- reorder の gap が埋まらず進捗が止まった場合と `overall_timeout` 到達時に `TimeoutError` を raise する
- テストを追加する (正常系 / reorder / idle タイムアウト / overall_timeout)

## 解決方法

(実装時に追記する)