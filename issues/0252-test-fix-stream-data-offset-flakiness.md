# QUIC の STREAM_DATA offset テストが pacing の期限待ちを行わず flaky に失敗する

- Created: 2026-09-21
- Completed: 2026-09-21
- Branch: feature/fix-stream-data-offset-flakiness
- Polished: {YYYY-MM-DD}

## 目的

`tests/test_quic_stream_data_offset.py` の `test_stream_data_offset_matches_cumulative_position` が CI の macOS で flaky に失敗し、canary のリリースが止まるため。

## 現状

- テストは 1000 バイトのチャンクを 5 個送信した後、`client.send()` / `server.send()` を 100 回固定で交換するだけで、ngtcp2 の pacing の期限待ちを行わない
- pacing 有効時は `send()` が送信可能期限まで `None` を返すため、ループが期限到達前に 100 回終わると最後のチャンクが送信されず、`assert expected_offset == sum(...)` が `4000 == 5000` で失敗する
- CI の run 35581534475 (canary 2026.1.0.dev20) では `test_macos` の 3.14 と 3.14t の両方がこの assert で失敗し、`create-release` / `publish_wheel` がスキップされて dev20 が未リリースになった。同一コミットを push した develop 側の run 35581527985 では成功しており、環境依存の flaky である
- 手元の macOS 26.4 (arm64) で旧コードを 50 回実行すると 19 回失敗した。デバッグ計測では 100 回の `client.send()` のうち 92 回が pacing で `None` を返した
- `tests/conftest.py` には pacing 対応のための `PUMP_ATTEMPTS` と `wait_pacing_timeout` があり、`tests/test_quic_conn_stats.py` などが同じ形で期限を待っている。本テストだけが未対応である

## 設計方針

- `tests/conftest.py` の `PUMP_ATTEMPTS` と `wait_pacing_timeout` を使い、両側の `send()` が `None` のときは期限まで待って再試行する (他のテストと同じイディオム)
- 全チャンクの受信を確認できた時点で交換ループを打ち切る。受信完了後も `wait_pacing_timeout` の上限 (50 ms) まで待ち続けると `PUMP_ATTEMPTS` 回で数十秒かかり得るため
- STREAM_DATA イベントの収集を交換ループ内へ移し、未達のままループが終わった場合は既存の offset 検証の assert が失敗するようにする (検証内容は変えない)

## 完了条件

- 修正後、手元の macOS で 200 回連続実行して全成功する
- CI (macOS 3.14 / 3.14t を含む) で失敗しない
- offset が累積位置と一致するという検証内容が変わらない
- 全テストが通過する

## 対象外

- pacing を無効化する API の追加 (ngtcp2 側の設定であり、本 issue では扱わない)
- 固定回数の送受信ループを持つ他のテストの一括見直し

## 解決方法

- `tests/test_quic_stream_data_offset.py` の `test_stream_data_offset_matches_cumulative_position` の交換ループを `PUMP_ATTEMPTS` 回にし、両側の `send()` が `None` のときは `wait_pacing_timeout` で期限まで待って再試行するようにした
- STREAM_DATA イベントの収集を交換ループ内へ移し、全チャンク (5000 バイト) の受信を確認できた時点でループを打ち切るようにした
- 検証: 修正前は手元の macOS 26.4 (arm64) で 50 回中 19 回失敗した。修正後は同じ環境で 200 回連続実行して全成功した。prek の pytest (全テスト) も通過した
