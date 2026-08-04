# セッション ID 集合の先頭要素に依存するフォールバックを修正する

- Created: 2026-08-04
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-fallback-session-id
- Polished: {YYYY-MM-DD}

## 目的

セッション ID 集合の先頭要素に依存するフォールバック 2 箇所を修正する。複数セッションを確立した構成では、先頭要素 (最小のセッション ID) はリセット・送信対象のストリームと無関係なセッションになり得る。STREAM_RESET 経路の同種の問題は 0009 で修正済みだが、別経路に同じパターンが残っている。

## 現状

- `src/webtransport/h3/server.py` の `_process_webtransport_events` の DATAGRAM 分岐は、`receive_datagram` で復元した `session_id` が負の場合に `get_session_ids()` の先頭要素を `on_datagram` に渡す
- `src/bindings/webtransport_h3.cpp` の `send_stream_data` は、`stream_info_` に未登録のストリームへの送信時、`session_ids_` の先頭要素をセッション ID として使う (複数セッション時に誤ったセッションにデータが属し得る)
- DATAGRAM 分岐のフォールバックは、`receive_datagram` が Quarter Stream ID (非負の varint) から `session_id = quarter_stream_id * 4` を復元するため、構造的に到達不能である可能性が高い (防御的コードとして残置されている)

## 設計方針

- DATAGRAM 分岐のフォールバック: 到達不能性を確認したうえで、セッション ID 集合の先頭要素依存をやめる (フォールバック自体を削除するか、復元失敗時は -1 を渡す形に変更する)
- `send_stream_data` のフォールバック: セッション ID 集合の先頭要素依存をやめ、復元できない場合は送信を諦める (-1 で登録しない) か、フォールバックの設計自体を見直す。実装時に既存のテスト (`tests/test_webtransport_h3_ack_offset.py` 等) との整合を確認する
- 変更対象は `src/webtransport/h3/server.py`、`src/bindings/webtransport_h3.cpp` / `.h`、テスト

## 完了条件

- フォールバック 2 箇所がセッション ID 集合の先頭要素に依存しなくなる
- モックなしのテストで、複数セッションを確立した構成でも誤ったセッション ID が渡らないことを確認する
