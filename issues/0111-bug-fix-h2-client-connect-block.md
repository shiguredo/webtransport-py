# h2.Client.connect() が非 2xx 拒否で永久ブロックする問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-client-connect-block
- Polished: {YYYY-MM-DD}

## 目的

高レベル `h2.Client.connect()` が、サーバーからの非 2xx 拒否 (403 等) で永久ブロックする問題を修正する。拒否時に SESSION_READY も SESSION_CLOSED も発火しないため、サーバーが接続を閉じない限りループが永遠に回り続ける。

## 現状

- `src/bindings/webtransport_h2.cpp` のクライアント側は非 2xx 応答受信時にセッションエントリを削除するだけで、SESSION_READY / SESSION_CLOSED のどちらも発火しない
- `src/webtransport/h2/client.py` の `Client.connect` は `while self._running` ループで SESSION_READY / SESSION_CLOSED のどちらかを待つため、拒否されたセッションで永久ブロックする
- タイムアウト引数も存在しない (QUIC 層は timeout 引数を持つ)
- 既存テスト `tests/test_webtransport_h2_reject_session.py` の非 2xx 拒否テストは Sans-IO 層のみで、高レベル connect() の動作は未カバー

## 設計方針

- 非 2xx 応答の受信を高レベル層で検知し、connect() が false を返すか拒否イベントを発火する
- 必要に応じて connect() にタイムアウトを追加する
- 高レベル connect() の拒否シナリオのテストを追加する

## 完了条件

- 非 2xx 拒否で connect() がブロックせず false を返す (または拒否が通知される)
- テストが追加される
