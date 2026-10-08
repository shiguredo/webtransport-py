# 巨大 Quarter Stream ID の DATAGRAM テストが pacing で送出が詰まり CI で失敗する

- Created: 2026-10-08
- Completed: 2026-10-08
- Branch: feature/test-fix-h3-low-level-pacing-timer
- Reporter: @voluntas

## 目的

CI が `tests/test_e2e_webtransport_h3_low_level.py` の
`test_datagram_invalid_session_id_closes_connection` で断続的に失敗するのを解消する。
2026-10-08 の wheel ワークフロー (macos-26_arm64 / 3.14) では 1346 テストが通過する中で
このテストだけが落ちた。

## 現状

- 失敗は `assert connection_closed is True` であり、サーバーの CONNECTION_CLOSE を
  クライアントが観測できない
- ローカル (macOS) で 30 回中 1 回、診断用の再現では 12 回中 1 回再現した
- 原因はテストの待機ループが QUIC のタイマーを進めないことにある。
  `send_datagram` の直後に 8 回フラッシュしても、`send()` が pacing の待ちの間は
  パケットを返さないため (診断時は 8 回すべて None、`get_timeout()` は 3280 ns)、
  データグラムが送信キューに残る。ngtcp2 のタイマー (pacing / PTO) は
  `handle_timeout()` で明示的に進める契約であり、ライブラリ本体の `Client.run` は
  満了したタイマーを進めてから再送信しているが、テストのループは `_receive()` と
  `asyncio.sleep()` だけで駆動していた
- 診断では、待機ループの後に `handle_timeout()` と `send()` を 1 回呼ぶだけで
  データグラムがワイヤに出て、サーバーが H3_ID_ERROR (0x0108) で接続を閉じた
  (クライアントの `error_code()` は 264)
- 影響: テストが実行タイミング依存になり、CI が確率的に失敗する。ライブラリの挙動は
  RFC 9000 と ngtcp2 の契約どおりであり、テスト側の駆動漏れである

## 設計方針

- ライブラリ側は変更しない
- `_LowLevelClient` に、満了した QUIC タイマーを進めて送信待ちを掃く `_advance_timers` を
  追加する。`send_datagram` 後のフラッシュと CONNECTION_CLOSE の待機ループの両方から
  呼び、pacing で止まっていたデータグラムが次のタイマー満了で確実に出るようにする
- 変更対象: `tests/test_e2e_webtransport_h3_low_level.py`

## 完了条件

- `test_datagram_invalid_session_id_closes_connection` が 60 回連続実行で失敗しない
- 全テストが通過する
- CI が緑に戻る

## 解決方法

- `_LowLevelClient` に `_advance_timers` を追加した。`get_timeout()` が満了
  (0 以下) していれば `handle_timeout()` を呼び、送信待ちを 1 パケット掃く
  (ライブラリ本体の `Client.run` と同じ駆動)
- `test_datagram_invalid_session_id_closes_connection` の `send_datagram` 後の
  フラッシュループと CONNECTION_CLOSE の待機ループの両方で `_advance_timers` を
  呼ぶようにした。待機ループの 1 周目 (受信タイムアウト 0.1 秒の後) には pacing の
  タイマーが満了しているため、送信キューに残ったデータグラムが確実にワイヤへ出る
- 修正後のテストは 60 回連続実行で失敗せず、全テスト (1347 件) を 2 回連続で通過した
