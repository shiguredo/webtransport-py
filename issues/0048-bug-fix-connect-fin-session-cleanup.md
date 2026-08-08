# CONNECT ストリームのクリーンクローズ (FIN) でセッション終了の後始末が行われないのを修正する

- Created: 2026-08-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-connect-fin-session-cleanup
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 6 のセッション終了条件の 1 つ目「the CONNECT stream is closed, either cleanly or abruptly, on either side」のうち、クリーンクローズ (FIN) でセッションが終了しても、セッション ID が管理集合 `session_ids_` に残り続け、アプリケーションへの終了通知 (`on_session_closed`) が発火しない問題を修正する。リセット (abrupt) 経路の後始末は open issue 0026 の対象であり、本 issue はクリーンクローズ (FIN) 経路の検知を担当する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::initialize` は nghttp3 の `end_stream` コールバックを登録していない (nghttp3 の `nghttp3_callbacks.end_stream` はストリームが FIN で閉じられたときに発火する)
- そのため、CONNECT ストリームの FIN 受信ではセッション終了の検知自体が発生せず、`SessionClosed` イベントが生成されない。高レベル `Server` の `on_session_closed` は呼ばれず、セッション ID は `session_ids_` に残り続ける
- CONNECT ストリームは `stream_info_` に登録されないため、FIN 時に発火する既存の `stream_close_cb` ではセッション ID を復元できない
- 0010 の設計方針で「FIN ではセッション終了の検知自体が発生しない。検知経路の追加は本 issue の対象外とする」と線引きされ、0026 でも対象外とされた未対応項目

## 設計方針

- `H3Session::initialize` に `end_stream` コールバックを登録し、CONNECT ストリームの FIN でセッション終了を検知して `session_ids_` から削除し、`SessionClosed` イベントを発火する (CONNECT ストリームの判定は 0026 と同じ `session_ids_` のメンバーシップで行う)
- 0026 が実施するリセット経路の後始末 (session_ids_ からの削除・SessionClosed 発火・stream_info_ の清掃) と同じ後始末を FIN 経路にも適用する (共通処理への集約を検討する)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (end_stream コールバックの登録とセッション終了検知)、`src/webtransport/h3/server.py` (必要に応じて)、テスト (`tests/test_e2e_webtransport_h3.py`。0026 のテスト構成を流用する)

## 完了条件

- CONNECT ストリームの FIN で `session_ids_` から削除され、`SessionClosed` イベントが発火し、高レベル `Server` の `on_session_closed` が呼ばれる
- モックなしのテストで検証できる (0026 のテスト構成を流用する)
