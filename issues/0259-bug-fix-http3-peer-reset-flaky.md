# e2e テスト test_same_batch_headers_before_reset_on_server が負荷時に flaky になる

- Created: 2026-09-27
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-peer-reset-flaky
- Polished: {YYYY-MM-DD}

## 目的

`tests/test_e2e_http3_peer_reset.py` の `test_same_batch_headers_before_reset_on_server` が 2026-09-27 の全体テスト実行で 1 回失敗した。このテストは「同一の受信バッチに完備した HEADERS とピア起点のリセットが並ぶ場合、先に到着した HEADERS のコールバックが `on_stream_reset` より先に呼ばれる」こと (closed 0246) を検証しており、失敗はバッチに HEADERS が入らない取りこぼしで起きている。検証したい性質 (通知順序) ではなくデータグラムの収集条件で落ちる状態を解消し、負荷時に CI が落ちないようにする。

## 現状

- 失敗時の出力は `AssertionError: コールバックの順序が逆転しています: ['reset']` または `[]` (`assert order == ["request", "reset"]`)。`on_request` が 1 回も呼ばれておらず、`['reset']` は `on_stream_reset` だけが呼ばれたこと、`[]` はどちらも呼ばれていないことを示す。いずれも収集したバッチに HEADERS を含むデータグラムが入っていない
- 2026-09-27 の全体テスト実行 (`uv run pytest tests/ -q --timeout=30`) で `[reset_only]` が 1 回失敗した (同じマシンで別の重い処理を並行実行していた負荷条件)
- 2026-09-28 の追加観測: 全体テスト実行で `[reset_only]` が 1 回、単体実行 (`tests/test_e2e_http3_peer_reset.py` 全体) で `[reset_and_close]` が 1 回、単体実行 (`test_same_batch_headers_before_reset_on_server` のみ) 20 回のうち 1 回 (`order == []`) 失敗した。当初の「単体 25 回では再現せず」より再現率が高く、負荷に依存せず単体でも再現する
- テストは DUT の run ループを止め、ピアの `request` (HEADERS) と `reset_stream` (RESET_STREAM) をソケットに溜めてから `_collect_datagrams` でデータグラムを集め、1 回の `_drain_quic_events` / `_process_http3_events` として処理する
- `_collect_datagrams` は最初のデータグラムを `_WAIT_LIMIT` (5.0 秒) まで待ち、その後は `_QUIET_SECONDS` (0.5 秒) の静穏時間が切れるまで集める。打ち切りが固定の静穏時間に依存するため、ピアが HEADERS / RESET をまだソケットへ書き込んでいない (送信キューに残っている) 場合や、ACK のみのデータグラムが先に届いて HEADERS / RESET が静穏時間を超えて遅れる場合に、バッチに必要なデータグラムが入らない。`[]` と `['reset']` のどちらも「必要なフレームが未到達」を意味し、機序 (未書き込みか遅延か) は一意に決まらない
- closed 0246 の解決方法にも「データグラムの収集は固定待機ではなく『読み取りが途切れるまで集める』方式にした (全 suite 実行時に 1 件が flaky になったため)」と記録があり、静穏時間方式は flaky 対策として導入されたもの。今回の観測は、その方式でも取りこぼすことを示す
- 同ファイルの他のテスト: `reset_and_close` のパラメータは同じ収集ヘルパを使う。クライアント側 DUT のテスト (`test_same_batch_headers_before_reset_on_client` / `test_reset_only_notifies_once_on_client`) は `_wait_until` による待機でこの収集ヘルパを使わないため、収集方式の変更対象外である
- 2026-09-28 の調査で判明した原因 (実測):
  1. `close_connection=True` のパラメータでは、ピアの run ループがリクエストと RESET_STREAM を送出する前に `peer._quic_connection.close(0, "bye")` が呼ばれると、未送出のストリームデータが破棄され、DUT のバッチに HEADERS / RESET が入らない (`order == []`)。接続を閉じる前に `_send_pending` で掃き出しても、掃き出し時点でフレームがまだピアの HTTP/3 層の送信キューに積まれていない場合があり、`order == ['reset']` (HEADERS のみ取りこぼし) が残る
  2. 静穏時間 (0.5 秒) による打ち切りだけでは、ACK のみのデータグラムが先に届いて HEADERS / RESET が遅れる場合を取りこぼす (`order == []` / `['reset']`)
- 部分的な改善として「リクエストと RESET_STREAM の送出後に `PUMP_ATTEMPTS` と `wait_pacing_timeout` を使ってピアの送信待ちを掃き出す」を入れたところ、負荷なしの単体実行で 1/20、CPU 負荷時で 8/10 の失敗が残った (改善前は負荷時 10/10)。ピアの HTTP/3 層へのキュー投入自体が run ループ任せで遅れる場合があり、掃き出しの1回では足りないことが原因と推定している

## 設計方針

- 番兵 (sentinel) データグラム方式は採らない。番兵が保証するのは「ピアのソケットへ書き込み済みのデータグラムの順序」だけで、`_send_pending` は輻輳ウィンドウ・フロー制御・pacing の期限待ちでも 0 を返すため、未書き込みの HEADERS / RESET を番兵が追い越す。実測でも 120 回中 15 回失敗し、失敗はすべて「番兵到着時のバッチに HEADERS が入らない」だった
- 打ち切り条件は「送信待ちの掃き出し + 短い静穏時間」にする。具体的には (1) ピアの run ループを止め、(2) `tests/conftest.py` の `PUMP_ATTEMPTS` と `wait_pacing_timeout` を使って `_send_pending` を pacing の期限まで掃き出し、(3) ソケットを読み切ってから短い静穏時間で打ち切る。掃き出しを先に行うことで、静穏時間は「送るものが無い」ことの確認として働く
- 掃き出しのタイミングは接続を閉じる前でなければならない。`peer._quic_connection.close()` は未送出のストリームデータを破棄するため、リクエストと RESET_STREAM を送出する前に閉じると、DUT のバッチから HEADERS / RESET が丸ごと消える (`order == []`)。現在は `reset_stream` の直後に掃き出し、その後に閉じる
- 1 回の `_drain_quic_events` / `_process_http3_events` で処理する形と「同一バッチで HEADERS が先」という検証内容は変えない。収集したデータグラムを複数回に分けて drain すると、検証したい性質 (同一バッチ内の通知順序) が別のものになるため選ばない
- 失敗メッセージには、集めたデータグラム数と `order` を常に含める。順序の表明だけを失敗させて原因が分からない状態にしない
- 変更対象: `tests/test_e2e_http3_peer_reset.py` (`_flush_peer_sends` / `_collect_datagrams` と対象テスト)。`tests/conftest.py` の `PUMP_ATTEMPTS` / `wait_pacing_timeout` は closed 0252 で追加済みのものを再利用し、conftest は変更しない。`CHANGES.md` は変更しない (CODEBASE.md の指示)
- 対象外: `src/bindings/http3.cpp` と `src/webtransport/http3/` の通知順序の実装 (closed 0246 で修正済み)、クライアント側 DUT のテスト 2 件 (`_wait_until` で待つ方式であり同じ収集ヘルパを使わない)
- 未解決の課題: 掃き出しを 1 回入れても、CPU 負荷時に `order == ['reset']` (HEADERS のみ未達) が残る。ピアの HTTP/3 層へのキュー投入自体が run ループ任せで遅れる場合があり、掃き出し時点でフレームがまだキューに無いことが原因と推定している。解決には「ピアの送信キューが空になったこと」を破壊的に確認せずに観測する手段 (テスト専用 API を含む) が必要で、この issue の残作業である

## 完了条件

- 修正前の失敗機序 (ピアのフレームが DUT のバッチに入らないこと) を再現する実行条件を特定し、その条件で RED と GREEN の両方を実測で示すこと。単純な回数頼みの検証は採らない (closed 0089 と同じ判断)
- CPU 負荷時 (別プロセスで CPU を使う) の単体実行と全体テスト実行で失敗しないこと。実行回数・負荷条件・結果を記録する
- 失敗した場合は、集めたデータグラム数と `order` が失敗メッセージから分かること
- 「同一バッチの完備 HEADERS がリセットより先に通知される」という検証内容が変わっていないこと (closed 0246 の RED が引き続き成立すること)
- 全テストが通過すること
