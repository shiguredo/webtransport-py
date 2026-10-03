# h2 のテストで WT_STREAM_STATE_ERROR の Error イベントを検証する表明をヘルパに集約する

- Created: 2026-09-18
- Branch: feature/refactor-consolidate-h2-error-event-assertions
- Polished: 2026-10-03

## 目的

`tests/test_webtransport_h2_stream_state_error.py` には、ワイヤの WT_CLOSE_SESSION を検証する
`_assert_state_error_sent` と、Error イベント (0x51) を検証する `_assert_error_event` の両方がある。
しかし `_assert_error_event` は解放済みストリーム検出の追加 (closed/0236) で新設されたテスト 4 件だけで
使われており、それ以前からある 8 箇所のテストは同じ絞り込みと表明をインラインで書き写している。
件数・`error_code`・`stream_id` の 3 点の検証がヘルパとインラインに分散しているため、検証粒度が
テストごとにばらつき、ワイヤ検証と揃えるときに片方だけ直し忘れる余地がある。本 issue はインラインの
8 箇所をヘルパへ移行して、0x51 の Error イベント検証をヘルパ 1 箇所に集約する。

## 現状

- `_assert_state_error_sent` はワイヤ側 (WT_CLOSE_SESSION の Application Error Code とメッセージ) を検証する
- `_assert_error_event(server, stream_id)` は、Error イベントが 1 件だけ通知され、`error_code` が
  WT_STREAM_STATE_ERROR で `stream_id` が期待値であることをヘルパで検証する。closed/0236 で
  `tests/test_webtransport_h2_stream_state_error.py` に追加され、解放済みストリーム系のテスト 4 件
  (test_wt_stream_after_release_sends_state_error / test_wt_reset_stream_after_release_sends_state_error /
  test_wt_stream_data_blocked_after_release_sends_state_error /
  test_wt_reset_stream_after_release_prefers_released_over_reliable_size) だけが使用している
- インラインの重複は 8 箇所ある。うち 6 箇所は件数・`error_code`・`stream_id` の 3 点を表明し、
  `test_wt_stream_after_fin_sends_state_error` と
  `test_wt_stream_flow_control_excess_on_terminal_stream_sends_state_error` は件数と `error_code`
  の 2 点のみで `stream_id` を表明していない
- ワイヤ検証とイベント検証が別々に書かれているため、検知経路を追加したときにワイヤだけ・イベントだけ
  を表明するテストが混在し得る (closed/0236 の追加で実際に、新規テストのヘルパ利用と既存テストの
  インライン残存という混在が生じた)
- `_assert_error_event` は `server` から `_drain_events` する形だが、上記 8 箇所のうち 2 回目の
  WT_STOP_SENDING 系の 3 箇所 (test_wt_stop_sending_second_sends_state_error /
  test_wt_stop_sending_duplicate_in_same_receive_sends_state_error /
  test_wt_stop_sending_unknown_stream_second_sends_state_error) は、1 回の `_drain_events` で取得した
  リストから STOP_SENDING 非発火と ERROR 発火を同時に検証している
- `tests/test_webtransport_h2_flow_control_capsule.py` の `h2.EventType.ERROR` 絞り込みは
  「Error イベントが発火しないこと」を表明する否定的表明であり、本 issue の対象とする 0x51 の
  3 点検証とは形が異なる。`tests/test_webtransport_h2_initiator_validation.py` と
  `tests/test_webtransport_h2_received_map_bound.py` は、それぞれファイル内の `_stream_state_errors`
  ヘルパで 0x51 のイベントを絞り込んでおり、検証の仕方 (エラーコードで絞る述語ヘルパ + 呼び出し側の
  件数・セッション終了の検証) が本 issue の対象と異なる。これらのファイルの扱いは本 issue の対象外とする

## 設計方針

- 移行先は既存の `_assert_error_event` を改名したヘルパとする。`_assert_error_event` は
  `_assert_state_error_sent` との対称性から `_assert_state_error_event` へ改名し、既存 4 箇所の
  呼び出しも合わせる。対象は 0x51 (WT_STREAM_STATE_ERROR) 専用のため、エラーコードは引数で受けず
  ヘルパ内に固定する (エラーコードを引数化する必要があるのは、error code が 0x50 や WT_ERROR の
  別系統のイベント検証であり、本 issue の対象外)
- ヘルパは `server` ではなく `_drain_events` 済みのイベントリストと期待する `stream_id` を受け取る形に
  変更する。現在の形のままでは、事前に `_drain_events` してから複数種別を検証している 2 回目の
  WT_STOP_SENDING 系の 3 箇所を移行できず、二重の drain で空リストになるため
- 置き場所は `tests/test_webtransport_h2_stream_state_error.py` 内のままとする。対象が同一ファイル内に
  限られるため `tests/conftest.py` への移動は行わない。`WT_STREAM_STATE_ERROR` 定数はファイル冒頭で
  import 済みの `WtErrorCode` を使う
- `stream_id` を現状表明していない 2 箇所もヘルパへ移行し、`stream_id` の表明が追加される
  (表明を弱めるのではなく強める)
- ワイヤ検証のみで Error イベントの表明が無いテストへの、イベント表明の追加は行わない (本 issue は
  重複解消が目的であり、検証強化は対象外)
- 挙動は変えない。表明の意味 (件数・`error_code`・`stream_id` の 3 点) は維持する

## 完了条件

- 次に列挙する 8 箇所のインラインの絞り込みと表明が、ヘルパ呼び出しに置き換わっている
  - test_wt_stream_after_fin_sends_state_error
  - test_wt_stream_flow_control_excess_on_terminal_stream_sends_state_error
  - test_wt_stop_sending_second_sends_state_error
  - test_wt_stop_sending_duplicate_in_same_receive_sends_state_error
  - test_wt_stop_sending_unknown_stream_second_sends_state_error
  - test_wt_max_stream_data_after_stop_sending_sends_state_error
  - test_wt_stream_data_blocked_for_terminal_stream_sends_state_error
  - test_wt_stream_data_blocked_after_reset_sends_state_error
- `tests/test_webtransport_h2_stream_state_error.py` に `h2.EventType.ERROR` で絞り込むインラインの
  表明が残っていない (ヘルパ定義を除く)
- ヘルパの検証内容は「Error イベントが 1 件だけ通知される・`error_code` が WT_STREAM_STATE_ERROR・
  `stream_id` が期待値」の 3 点を維持する
- 全テストが通過する
