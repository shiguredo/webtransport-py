# h3 低レベル e2e のサーバー側イベント待ちが QUIC 再送を駆動せず CI で flaky に失敗する

- Created: 2026-10-13
- Completed: {YYYY-MM-DD}
- Branch: feature/test-fix-h3-low-level-server-event-wait
- Reporter: @voluntas

## 目的

CI の wheel ワークフローで断続的に失敗する
`tests/test_e2e_webtransport_h3_low_level.py::test_server_close_session_unknown_target_is_noop`
を解消する。検証したい性質 (未登録アドレス・未確立セッション ID への `close_session`
が何もしないこと) ではなく、送受信の駆動不足で落ちる状態を解消し、CI の信頼性を
高める。

## 現状

- 2026-10-13 の wheel ワークフロー (run 38340278692 / job 115109820791、
  `macos-26_arm64` / Python 3.15) で 1465 テストが通過する中で
  `test_server_close_session_unknown_target_is_noop` が 1 件だけ失敗した。
  失敗地点は `on_stream_data` のイベント待ち (`info.data_received`) の
  5 秒タイムアウトで、`close_session` の呼び出し自体は成功している
- 同一コミット `62e4a2b` の develop 側の run 38340274908 は全テスト成功しており、
  コード回帰ではなく flaky である
- `tests/test_e2e_webtransport_h3_low_level.py` の `_LowLevelClient` は自前の
  QUIC ループを持ち、各テストが送受信を明示的に駆動する。サーバー側コールバックの
  イベント (`info.sessions_ready` / `info.data_received` / `info.reset_received` /
  `info.session_closed`) を待つ箇所は
  `asyncio.wait_for(info.xxx.wait(), timeout=5.0)` で待つだけで、待機中に
  `client` 側の QUIC ループを回していない
- `_LowLevelClient._pump()` は `send()` を 1 回呼んで 1 パケットだけ送出する。
  送出したパケットが失われても、ngtcp2 の再送タイマーは `handle_timeout()` を
  明示的に呼ばない限り進まない。待機中はこれが呼ばれないため再送が駆動されず、
  UDP ループバックでのパケット 1 つの損失で待ちがタイムアウトし得る
- この駆動不足は同ファイルの他のサーバー側イベント待ち箇所にも共通する

## 設計方針

- `_LowLevelClient` に、サーバー側イベントを待ちながら受信と満了タイマーを
  処理する待機メソッドを追加する。待機中は `_receive()` と `_advance_timers()` を
  回し、再送とタイマー処理を進める (ライブラリ本体の
  `src/webtransport/_h3_client.py` の `_H3Client.run` と同じ駆動)
- h3 層の送信キューは掃かない (`_pump()` を呼ばない)。`open_stream` しただけで
  WT ヘッダーを送らない前提の検証 (`test_stream_reset_before_data_received_minus_one`)
  の決定性を壊さないため
- 同ファイルの `asyncio.wait_for(info.*.wait(), timeout=5.0)` を新しい待機
  メソッドへ置き換える

## 完了条件

- `test_server_close_session_unknown_target_is_noop` を含む
  `tests/test_e2e_webtransport_h3_low_level.py` のテストが連続して通過すること
- 全テストが通過すること
- 追加した待機メソッドが再送を駆動することを確認できること

## 解決方法

{どのように対応するのかを明確にすること}
