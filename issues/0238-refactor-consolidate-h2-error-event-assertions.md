# h2 のテストで WT_STREAM_STATE_ERROR の Error イベントを検証する表明をヘルパに集約する

- Created: 2026-09-18
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-consolidate-h2-error-event-assertions
- Polished: {YYYY-MM-DD}

## 目的

`tests/test_webtransport_h2_stream_state_error.py` では、ワイヤの WT_CLOSE_SESSION を検証する `_assert_state_error_sent` はあるが、対になる「Error イベント (0x51) が通知されること」を検証するヘルパが無い。そのためテストごとに同じ 4 行のフィルタと 3 つの表明を書き写しており、片方だけ直し忘れる余地がある。

## 現状

- `_assert_state_error_sent` はワイヤ側 (WT_CLOSE_SESSION の Application Error Code とメッセージ) を検証する
- イベント側は各テストが `[event for event in _drain_events(server) if event.type == h2.EventType.ERROR]` で絞り込み、件数・`error_code`・`stream_id` を個別に表明している。同じ形が `tests/test_webtransport_h2_stream_state_error.py` に複数箇所ある
- `tests/test_webtransport_h2_flow_control_capsule.py` と `tests/test_webtransport_h2_initiator_validation.py` にも `h2.EventType.ERROR` を絞り込む類似の表明がある
- ワイヤ検証とイベント検証が別々に書かれているため、検知経路を追加したときにワイヤだけ・イベントだけを表明するテストが混在し得る (直近の追加でも実際に混在が生じた)

## 設計方針

- エラーコードと期待する `stream_id` を受け取るヘルパ (例: `_assert_state_error_event(server, stream_id)`) を追加し、既存の重複表明を置き換える
- 置き場所は `tests/test_webtransport_h2_stream_state_error.py` を第一候補とする。複数ファイルから使う場合は `tests/conftest.py` へ置き、エラーコード定数の所在 (`WtErrorCode`) との整合を取る
- ワイヤ検証 (`_assert_state_error_sent`) とイベント検証を 1 つのヘルパにまとめるかは、メッセージ引数の有無で呼び分けている現状を踏まえて決める。まとめる場合は「ワイヤだけ検証したい」「イベントだけ検証したい」ケースを潰さないこと
- 挙動は変えない。表明の意味を弱めないこと (件数・`error_code`・`stream_id` の 3 点は維持する)

## 完了条件

- 重複していた Error イベントの表明がヘルパ呼び出しに置き換わっている
- 表明の意味 (件数・`error_code`・`stream_id`) が弱まっていない
- 全テストが通過する
