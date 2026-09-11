# CI で flaky に失敗する残りのテストを修正する

- Created: 2026-09-12
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-remaining-ci-flaky-tests
- Polished: {YYYY-MM-DD}

## 目的

closed/0200 で CI の flaky 対策 (pacing 対応のポーリング化・閾値の緩和) を行ったが、その後も CI で 2 件の flaky 失敗が観測されている。再実行で回避する運用をなくし、CI の信頼性を高める。

## 現状

- develop への #144 マージ後の wheel 実行 (run 34628553098 / job 103360104795、macOS 3.14) で `tests/test_quic_stream_control.py::test_extend_max_stream_offset` が失敗した。失敗内容は `assert 362144 == ((262144 - 1) + 110001)` で、100,000 拡張時のピア側の増分が期待の 110,001 ではなく 100,001 だった
- PR #147 の wheel 実行 (run 34639839122 / job 103397024697、macOS 3.14) で `tests/test_quic_error_handling.py::test_connection_close_retransmission_on_receive` が失敗した。失敗内容は `assert ReceiveResult.CLOSED == ReceiveResult.DISCARDED` で、closing 中の 2 回目の受信が破棄ではなく終了として返った
- どちらも同じ実行の他のジョブ (macOS 3.14t / Ubuntu の各 2 環境) では成功しており、実装の不具合ではなくテストのタイミング依存である
- closed/0200 の失敗一覧に両テストが 1 回ずつ記録されているが、当時は「旧コードでの観測で現行コードでは pacing 対応済み」として対象外とされた。現行コードで再発したため、原因を特定して修正する

### 原因 (実験で確認済み)

1. `tests/test_quic_stream_control.py` の `exchange_packets` は最大 10 ラウンドの即時送受信で、`send()` が pacing で空振りしても待たずに打ち切る。ハンドシェイク直後などはクライアントの送信が pacing で空振りすることがあり、`test_extend_max_stream_offset` の最初の `exchange_packets` がストリームデータをサーバーへ届けないまま次の処理へ進む
2. サーバーがまだストリームを認識していない状態で `extend_max_stream_offset` を呼ぶと、ngtcp2 は不明ストリームに対して 0 (成功) を返して拡張を黙って破棄する (`_deps/ngtcp2` の `ngtcp2_conn_extend_max_stream_offset` は `ngtcp2_conn_find_stream` が NULL の場合に 0 を返す)。その後の拡張は破棄された 10,000 を含まず、`362144` になる (実験で現行コードのまま再現)
3. `test_connection_close_retransmission_on_receive` は closing 期間中に `wait_pacing_timeout` でクライアントの pacing 期限を待つ。closing 期間は `close()` 時に `3 * PTO` で固定され (`QuicConnection::close` の `closing_expiry_ns_`)、実験環境では約 78 ms しかない。待機がこれを超えると `QuicConnection::receive` の `closing_period_expired()` で `ReceiveResult.CLOSED` が返り、再送されない (実験で再現)
4. 対策の方向は実験で確認済み: `close()` の前にクライアントのパケットを 2 つ生成しておき、closing 中は生成済みパケットの配信と `send()` の確認だけを行うと、2 回とも `ReceiveResult.DISCARDED` と同一 CONNECTION_CLOSE の再送になる

## 設計方針

- `tests/test_quic_stream_control.py` の `exchange_packets` を pacing 対応にする。`send()` が None でも送信待ちが残る場合は `wait_pacing_timeout` で期限まで待って再試行し、`PUMP_ATTEMPTS` を上限とする。無条件の即時打ち切りをやめる
- `test_extend_max_stream_offset` は、拡張の前提 (サーバーがストリームを認識していること) を送受信で確定させてから `extend_max_stream_offset` を呼ぶ
- `test_connection_close_retransmission_on_receive` は closing 期間中の実時間待機を排除する。2 回の受信に使うクライアントパケットを `close()` の前に (pacing 待ち込みで) 生成し、closing 中は生成済みパケットの配信と `send()` の確認だけを行う
- flaky の再発防止として、失敗時にどの前提が崩れたかを示す表明とコメントを入れる
- 変更対象: `tests/test_quic_stream_control.py` / `tests/test_quic_error_handling.py` (必要なら `tests/conftest.py` の共通ヘルパー)。テストのみの変更のため `CHANGES.md` は更新しない (closed/0200 と同じ)

## 完了条件

- `test_extend_max_stream_offset` と `test_connection_close_retransmission_on_receive` がローカルで 20 回連続して通過すること
- 全テストが通過すること
- CI (wheel ワークフロー) が通過すること
