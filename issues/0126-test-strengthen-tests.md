# PBT の網羅強化と flaky 解消

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/add-strengthen-tests
- Polished: 2026-09-05

## 目的

PBT (Property-Based Testing) で検証できるプロトコル不変条件のうち未対応分を property 化し、flaky 要因を解消して回帰を早期に検出できるようにする。

## 現状

- **エラーパスのテスト欠落**: run() ハング系は closed の 0107 / 0113 / 0131 で回帰テスト済みのため本 issue の対象外とする
- **フロー制御カプセルのテスト欠落**: H2 減少値検出は closed の 0097 で対応済みのため本 issue の対象外とする。H3 セッションフロー制御の制限超過は 0092 側で検証し、本 issue では当該不変条件の property 化のみを扱う。重複する場合は PBT に寄せる
- **境界値テストの欠落**: H2 / H3 の WT_CLOSE_SESSION 1024 バイト超・不正 UTF-8 は closed の 0100 / 0096 で、error_code 32bit 範囲超過は closed の 0101 で、過大データグラムは closed の 0109 で対応済みのため本 issue の対象外とする
- **PBT の偏り**: `tests/prop_webtransport_h3.py` / `prop_webtransport_h2.py` の大半は弱不変条件の検証であり、`tests/prop_isolation_h2.py` / `prop_isolation_h3.py` は独立性のみを検証する。確立済み送信のキュー積載確認 (H3 `send_datagram`)・`ValueError` 検証 (H2 `reject_session`)・UTF-8 切り詰めの property 化済み分を除き、close 後に明示的生成した close 済みセッション ID 宛の送信無視 (既存の任意 ID 無視とは手順が異なる) とフロー制御超過のエラー送出が未 property 化である
- **flaky 要因**: `tests/test_e2e_webtransport_h2.py` の `test_session_close_notifies_server` における `run_client` タスク起動後・`close()` 前の `asyncio.sleep` はイベント待ちへの置換候補である (H3 側の同名テストはイベント待ち構成のため対象外)。`test_client_on_session_ready_fires` と `test_client_on_session_ready_after_connect` における `ready_event` 待機後の単発発火確認前の `asyncio.sleep` 2 箇所は追加発火がないことの確認のための settle 待ちであり、置換対象外とする。`tests/test_e2e_webtransport_h3.py` の `test_large_echo_over_initial_recv_window` における `echo_completed` 待ちの `wait_for` が 60 秒であり CI の `--timeout=30` と不整合 (先に pytest-timeout が殺す) である

## 設計方針

- 各修正 issue (0092 / 0122) 側で追加されるテストは本 issue の対象外とし、本 issue は PBT の property 化と flaky 解消に限定する。PBT 追加と flaky 解消は同一 e2e / PBT ファイル群への変更で競合回避のため同一 issue で扱い、独立に PR 分割可能とする。0122 項目 4 による `close()` 書き換えと競合した場合は 0122 を優先しリベースする。フロー制御超過の property 化は 0092 完了後に着手する
- close 後に明示的生成した close 済みセッション ID 宛の送信無視とフロー制御超過のエラー送出を property 化する。追加する property に対応する draft (H2 は draft-ietf-webtrans-http2-15、H3 は draft-ietf-webtrans-http3-16) の節番号を `refs/` パス付きでコードコメントに残す。`shiguredo-python` の役割分担に従い、PBT で書けるものは単体テストで重複させない
- `tests/test_e2e_webtransport_h2.py` の `test_session_close_notifies_server` の sleep を、`close()` 前に成立し `run()` 未起動では成立しない観測を満たすイベント待ちに置き換える。置換先イベント名を本 issue で確定させる。settle 用 2 箇所は意図維持のため残す。`test_large_echo_over_initial_recv_window` の `wait_for` を 5.0 秒を第一候補として 10 秒以内 (CI の 30 秒未満と `shiguredo-python` の上限を満たす範囲) で確定させる

## 完了条件

- H2 / H3 の各 PBT ファイルに close 済み送信無視とフロー制御超過の property が計 2 件追加され、全テストが通る
- `tests/test_e2e_webtransport_h2.py` の `test_session_close_notifies_server` の sleep が確定したイベント待ちに置換され、settle 用 2 箇所の sleep が残存し settle 目的のコメントが付与される
- `test_large_echo_over_initial_recv_window` の `wait_for` が確定値 (5.0 秒第一候補) になり、全テストが通る
- `tests/test_e2e_webtransport_h2.py::test_session_close_notifies_server` と `tests/test_e2e_webtransport_h3.py::test_large_echo_over_initial_recv_window` が `uv run pytest tests/ -v --timeout=30` の 10 回連続および `uv run pytest tests/test_e2e_webtransport_h2.py::test_session_close_notifies_server tests/test_e2e_webtransport_h3.py::test_large_echo_over_initial_recv_window -v --timeout=30` の 50 回連続で安定する
