# WebTransport over HTTP/3 の低レベル API テストを分割する

- Created: 2026-08-08
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-split-e2e-h3-tests
- Polished: {YYYY-MM-DD}

## 目的

`tests/test_e2e_webtransport_h3.py` が約 2,500 行に肥大化し、低レベル API クライアント (`_LowLevelClient`) とそれを利用するリセット系・データグラム系テスト群が高レベル API (Client / Server) のテストと同居して保守性が低下している。低レベル API 構成のテストを別ファイルへ分割して可読性と保守性を高める。

## 現状

- `tests/test_e2e_webtransport_h3.py` は約 2,500 行で、高レベル API (Client / Server) のテストに加え、同一 QUIC 接続上に複数セッションを確立する低レベル API 構成 (`_LowLevelClient` クラス、約 250 行) とそれを利用するテスト (STREAM_RESET 系・RESET_STREAM_AT・データグラムの負のセッション ID 等) が同居している
- `_LowLevelClient` は `quic.Connection` + `h3.Session` を直接構築する接続手順 (ハンドシェイク・SETTINGS 待ち・制御ストリームのバインド) と、複数セッション確立・ストリーム操作・パケット保留などのヘルパーを含む

## 設計方針

- `tests/test_e2e_webtransport_h3.py` から低レベル API 構成のテスト群と `_LowLevelClient` を別ファイルへ分割する
- 分割先のファイル名は既存のテスト配置規約に従う (例: `tests/test_e2e_webtransport_h3_low_level.py` など)
- テストの動作・アサーションは変更しない (純粋なリファクタリング。`### misc` 相当の変更として `[UPDATE]` で CHANGES.md に記載する)
- 0028 の接続ヘルパー共通化 (conftest.py への集約) と干渉しない配置を検討する

## 完了条件

- 低レベル API を使うテストが別ファイルへ移動し、`tests/test_e2e_webtransport_h3.py` が高レベル API のテストに集約される
- 全テストが通る
