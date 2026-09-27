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
- `_collect_datagrams` は最初のデータグラムを `_WAIT_LIMIT` (5.0 秒) まで待ち、その後は `_QUIET_SECONDS` (0.5 秒) の静穏時間が切れるまで集める。打ち切りが固定の静穏時間に依存するため、ACK のみのデータグラムが先に届いてから HEADERS / RESET を含むデータグラムが静穏時間を超えて遅れると、バッチに必要なデータグラムが入らない (`[]` は ACK のみを集めた場合、`['reset']` は RESET までを集めて HEADERS を取りこぼした場合に対応する)
- closed 0246 の解決方法にも「データグラムの収集は固定待機ではなく『読み取りが途切れるまで集める』方式にした (全 suite 実行時に 1 件が flaky になったため)」と記録があり、静穏時間方式は flaky 対策として導入されたもの。今回の観測は、その方式でも取りこぼすことを示す
- 同ファイルの他のテスト: `reset_and_close` のパラメータは同じ収集ヘルパを使う。クライアント側 DUT のテスト (`test_same_batch_headers_before_reset_on_client` / `test_reset_only_notifies_once_on_client`) は `_wait_until` による待機でこの収集ヘルパを使わないため、収集方式の変更対象外である
- 2026-09-28 の調査で判明した原因 (実測):
  1. `close_connection=True` のパラメータでは、ピアの run ループがリクエストと RESET_STREAM を送出する前に `peer._quic_connection.close(0, "bye")` が呼ばれると、未送出のストリームデータが破棄され、DUT のバッチに HEADERS / RESET が入らない (`order == []`)。接続を閉じる前に `_send_pending` で掃き出しても、掃き出し時点でフレームがまだピアの HTTP/3 層の送信キューに積まれていない場合があり、`order == ['reset']` (HEADERS のみ取りこぼし) が残る
  2. 静穏時間 (0.5 秒) による打ち切りだけでは、ACK のみのデータグラムが先に届いて HEADERS / RESET が遅れる場合を取りこぼす (`order == []` / `['reset']`)
- 部分的な改善として「リクエストと RESET_STREAM の送出後に `PUMP_ATTEMPTS` と `wait_pacing_timeout` を使ってピアの送信待ちを掃き出す」を入れたところ、負荷なしの単体実行で 1/20、CPU 負荷時で 8/10 の失敗が残った (改善前は負荷時 10/10)。ピアの HTTP/3 層へのキュー投入自体が run ループ任せで遅れる場合があり、掃き出しの1回では足りないことが原因と推定している

## 設計方針

- 打ち切り条件を「静穏時間」から「ピアが送出し終えたことを示す番兵 (sentinel) データグラムが届くまで (上限時間 `_WAIT_LIMIT` 付き)」に変える。ピア (`http3.Client`) は自身のソケット (`_socket`) を持ち、QUIC のパケットも番兵も同じソケットから送られるため、loopback の UDP では送出順に届く。番兵が観測できた時点で先行する QUIC パケットはすべて DUT のソケットに到着しており、バッチが確定する。番兵は収集対象から除き DUT へ渡さない (`_read_available` で読んだ番兵はバッチに含めない)
- 番兵方式を採る理由: `http3.Client._send_pending` は送信パケット数を返すが、`request` / `reset_stream` が内部で送出する分をテストから数えられない。送出台数を返すテスト専用 API を足す方法もあるが、実装に手を入れずテスト側だけで完結する番兵方式を優先する
- 上限時間に達しても番兵が届かない場合は、集めたデータグラム数と `order` の内容を失敗メッセージに含める。順序の表明だけを失敗させて原因が分からない状態にしない
- 1 回の `_drain_quic_events` / `_process_http3_events` で処理する形と「同一バッチで HEADERS が先」という検証内容は変えない。収集したデータグラムを複数回に分けて drain すると、検証したい性質 (同一バッチ内の通知順序) が別のものになるため選ばない
- 変更対象: `tests/test_e2e_http3_peer_reset.py` (`_collect_datagrams` と対象テスト)。`CHANGES.md` は変更しない (CODEBASE.md の指示)
- 対象外: `src/bindings/http3.cpp` と `src/webtransport/http3/` の通知順序の実装 (closed 0246 で修正済み)。今回の失敗はテスト側の収集条件で説明でき、`[]` と `['reset']` はどちらもバッチに必要なデータグラムが入っていないことを示す

## 完了条件

- 対象テスト (`[reset_only]` と `[reset_and_close]` の両方) を、修正前は失敗した実行条件 (単体の繰り返し実行と全体テスト実行) で失敗しないこと。実行回数と結果を記録する
- 番兵が届かない場合は、集めたデータグラム数と `order` が失敗メッセージから分かること
- 「同一バッチの完備 HEADERS がリセットより先に通知される」という検証内容が変わっていないこと (closed 0246 の RED が引き続き成立すること)
- 全テストが通過すること
