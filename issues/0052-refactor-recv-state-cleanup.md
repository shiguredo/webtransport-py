# QUIC クライアントのストリーム受信状態の破棄手段を追加する

- Created: 2026-08-08
- Completed: YYYY-MM-DD
- Branch: feature/refactor-recv-state-cleanup
- Polished: {YYYY-MM-DD}
- Reporter: @voluntas

## 目的

`recv_stream_data` (0037) で導入したストリーム受信状態 (`_StreamRecvState`) が接続寿命まで無制限に成長する問題を解消する。受信状態の破棄手段を追加し、メモリ使用量がストリーム数・受信データ量に比例して無制限に増加しないようにする。

## 現状

- `src/webtransport/quic/client.py` の `_StreamRecvState` はストリームごとの受信データ (`data`)、FIN 検出 (`fin`)、待機者通知イベント (`event`) を保持する
- `_update_recv_state` は STREAM_DATA 受信のたびに `_recv_states.setdefault(stream_id, ...)` で状態を作成し、`_recv_states` から削除する経路が存在しない
- `_handle_stream_reset` も STREAM_RESET 受信のたびに `_recv_states.setdefault` で状態を作成するため、データを一度も受信していないストリームでも RESET 受信ごとにエントリが新規作成される
- 結果として、FIN 完了済みのストリームも含め全ストリームの受信データが接続寿命まで保持され続ける
- `recv_stream_data` を一度も呼ばないコールバック専用の利用 (`on_stream_data`) でも全ストリームの全受信データが保持されるため、長期間の接続で大量データを扱うとメモリが無制限に増加する
- 「FIN 完了済みストリームの即時 return」要件 (0037) のため受信データの保持自体は必要だが、破棄手段が無い点が問題

## 設計方針

- 変更対象は `src/webtransport/quic/client.py` の高レベル `Client`
- 受信状態の破棄手段を追加する。方針は次のいずれか (実装時に決定する):
  - `recv_stream_data` が正常 return したストリームの受信状態を破棄する
  - 明示的な破棄 API (例: `discard_recv_state(stream_id)`) を追加する
  - 受信状態の保持数を上限で制限する
- 破棄する場合、「FIN 完了済みストリームの即時 return」要件 (0037) との整合を取る。破棄後は通常の待機にフォールバックする (既に受信済みデータは返せない) 旨を明記する
- コールバック専用のストリーム (`recv_stream_data` を呼ばない) では受信状態を作成しない、または受信データを保持しない方針も検討する

## 完了条件

- `_recv_states` が破棄手段を持ち、ストリーム完了後にメモリが解放される
- 破棄後の `recv_stream_data` の挙動がドキュメントに明記される
- 受信状態の破棄・保持に関するテストを追加する
- 既存の全テストが通る

## 解決方法

(実装時に追記する)
