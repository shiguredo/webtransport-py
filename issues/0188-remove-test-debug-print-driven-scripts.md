# print 駆動のデバッグテスト test_debug_*.py 3 本を削除する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/remove-test-debug-print-driven-scripts
- Polished: {YYYY-MM-DD}

## 目的

`tests/test_debug_quic.py` / `test_debug_quic_handshake.py` / `test_debug_webtransport_h3.py` の 3 本は `print(...)` を主体としたデバッグドライバで、assert が 0 または英語メッセージ付き。shiguredo-python 規約「print デバッグをコミットしないこと」「テストのログメッセージは日本語」を違反しており、内容は既存の `test_e2e_*` / `test_quic*.py` / `test_webtransport_h3_*.py` が既にカバーする。削除して規約整合を回復する。

## 現状

- `tests/test_debug_quic.py` (全 131 行): `print` 24 箇所、`test_quic_client_send` (25-41) は assert 0 件、末尾に `if __name__ == "__main__":` を持つ「実行スクリプト」形式
- `tests/test_debug_quic_handshake.py` (全 129 行): `print` 30 箇所、`assert success, "Handshake should complete"` (英語メッセージ規約違反)、`create_test_certificates()` を毎回呼び `mkdtemp` を放置
- `tests/test_debug_webtransport_h3.py` (全 318 行): `print` 33 箇所、SETTINGS 受信判定 `stream_id == 3` の複製 (issue 0180 と関連)
- 3 本合計 88 個の `print` 呼び出しは規約「print デバッグをコミットしないこと」違反 (集計 88 / tests 内 print 88 と一致)
- 内容の重複: `test_debug_quic.py` の handshake / send は `test_quic.py` と `conftest.perform_handshake` で、`test_debug_quic_handshake.py` は `tests/prop_quic_handshake.py` と、`test_debug_webtransport_h3.py` は `test_e2e_webtransport_h3.py` の `_LowLevelClient` 経路と重複
- pytest 収集対象に含まれ CI 時間を消費するが、失敗しても診断情報は print で流れるだけで CI ログでは追跡困難

## 設計方針

- 3 ファイルを削除する
- 削除に伴い、以下のカバレッジがどこかで維持されているかを事前確認する:
  - QUIC ハンドシェイクの実行確認 → `tests/test_quic.py` / `conftest.perform_handshake` で維持済み
  - `test_debug_webtransport_h3.py` の SETTINGS 判定 (`stream_id == 3` の複製) → issue 0180 で `is_webtransport_ready()` に置き換え後、複製そのものが消える
  - 過去のデバッグ経験値としての残置価値は git 履歴に残るため削除しても失われない
- `print(f"ngtcp2 version: ...")` (`test_quic.py:23`)、`print(f"nghttp2 version: ...")` (`test_http2.py:25`)、`print(f"nghttp3 version: ...")` (`test_http3.py:19`) は本 issue の範囲外 (単体で削除するかは別途判断)

## 完了条件

- `tests/test_debug_*.py` の 3 ファイルが削除されていること
- pytest 収集が対応する 3 ファイル分減ること
- カバレッジが劣化しないこと (削除前後で pytest 全件通過を維持)
- 既存のテスト全 819 件 (削除で -3 の可能性) が引き続き通過すること
