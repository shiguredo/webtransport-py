# e2e テスト test_same_batch_headers_before_reset_on_server が負荷時に flaky になる

- Created: 2026-09-27
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-peer-reset-flaky
- Polished: {YYYY-MM-DD}

## 目的

`tests/test_e2e_http3_peer_reset.py` の `test_same_batch_headers_before_reset_on_server` が 2026-09-27 の全体テスト実行で 1 回失敗した。このテストは「同一の受信バッチに完備した HEADERS とピア起点のリセットが並ぶ場合、先に到着した HEADERS のコールバックが `on_stream_reset` より先に呼ばれる」こと (closed 0246) を検証しており、失敗はバッチに HEADERS が入らない取りこぼしで起きている。検証したい性質 (通知順序) ではなくデータグラムの収集条件で落ちる状態を解消し、負荷時に CI が落ちないようにする。

## 現状

- 失敗時の出力は `AssertionError: コールバックの順序が逆転しています: ['reset']` (`assert order == ["request", "reset"]`)。`on_request` が 1 回も呼ばれておらず、`on_stream_reset` だけが呼ばれている
- 観測は 2026-09-27 の `uv run pytest tests/ -q --timeout=30` の 1 回のみ (同じマシンで別の重い処理を並行実行していた負荷条件)。同テストの単体実行 25 回と、その後の全体テスト 2 回では再現していない
- テストは DUT の run ループを止め、ピアの `request` (HEADERS) と `reset_stream` (RESET_STREAM) をソケットに溜めてから `_collect_datagrams` でデータグラムを集め、1 回の `_drain_quic_events` / `_process_http3_events` として処理する
- `_collect_datagrams` は最初のデータグラムを `_WAIT_LIMIT` (5.0 秒) まで待ち、その後は `_QUIET_SECONDS` (0.5 秒) の静穏時間が切れるまで集める。打ち切りが固定の静穏時間に依存するため、負荷時に HEADERS を含むデータグラムの到着が 0.5 秒を超えて遅れると、バッチに RESET だけが入り `on_request` が発火しない
- closed 0246 の解決方法にも「データグラムの収集は固定待機ではなく『読み取りが途切れるまで集める』方式にした (全 suite 実行時に 1 件が flaky になったため)」と記録があり、静穏時間方式は flaky 対策として導入されたもの。今回の観測は、その方式でも負荷時に取りこぼすことを示す
- 同ファイルの他のテスト (`reset_and_close` のパラメータと、クライアント側 DUT のテスト) も同じ収集ヘルパを使う

## 設計方針

- 打ち切り条件を「静穏時間」から「ピアが送出したデータグラム数に届くまで (上限時間付き)」に変える。ピアの送出はテストが駆動しているため、送信経路が送出したデータグラム数をテストから観測できるようにすれば、DUT のソケットに届くべき数が確定する (ACK のみのデータグラムが混ざっても数えれば済むため、QUIC パケットの復号は不要)。観測手段は既存の送信経路が公開する計数を使うか、`http3.Client` / `http3.Server` に送出台数を返すテスト専用 API を足す (CODEBASE.md の「テスト専用 API であっても細粒度の機能を惜しまず用意する」に沿う)。複数のパケットを 1 つの UDP データグラムにまとめて送出できるなら、その経路でバッチを確定させる案も比較検討する
- 上限時間 (`_WAIT_LIMIT`) に達しても必要な数に届かない場合は、集めたデータグラム数・待っていた数・`order` の内容を失敗メッセージに含める。順序の表明だけを失敗させて原因が分からない状態にしない
- 1 回の `_drain_quic_events` / `_process_http3_events` で処理する形と「同一バッチで HEADERS が先」という検証内容は変えない。収集したデータグラムを複数回に分けて drain すると、検証したい性質 (同一バッチ内の通知順序) が別のものになるため選ばない
- 変更対象: `tests/test_e2e_http3_peer_reset.py` (`_collect_datagrams` と対象テスト)、必要なら `tests/conftest.py` と `src/webtransport/http3/` のテスト専用 API。`CHANGES.md` は変更しない (CODEBASE.md の指示)
- 対象外: `src/bindings/http3.cpp` と `src/webtransport/http3/` の通知順序の実装 (closed 0246 で修正済み)。今回の失敗はテスト側の収集条件で説明でき、`['reset']` は `on_request` が呼ばれていないことだけを示す

## 完了条件

- 対象テストを負荷をかけた状態 (別プロセスで CPU を使う等) で繰り返し実行しても失敗しないこと。負荷条件と実行回数を記録する
- 取りこぼしが起きた場合は、集めたデータグラム数・待っていた数・`order` が失敗メッセージから分かること
- 「同一バッチの完備 HEADERS がリセットより先に通知される」という検証内容が変わっていないこと (closed 0246 の RED が引き続き成立すること)
- 全テストが通過すること
