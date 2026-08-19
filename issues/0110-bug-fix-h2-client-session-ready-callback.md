# h2.Client の on_session_ready コールバックが発火しない問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-client-session-ready-callback
- Polished: {YYYY-MM-DD}

## 目的

高レベル `h2.Client` で `on_session_ready` コールバックが一度も呼ばれない問題を修正する。connect() が SESSION_READY イベントを消費してしまうため、公開コールバックが機能していない。

## 現状

- `src/webtransport/h2/client.py` の `Client.connect` は 200 OK 待ちループで `next_event()` を取り出し、SESSION_READY を検知した時点で `_connected = True; return True` する (イベントを消費)
- 同じ `Client.run` のイベントループが `_on_session_ready` を呼ぶのは SESSION_READY イベント受信時だが、connect() が常に消費済みのため、単一セッション利用ではコールバックが一度も呼ばれない
- サーバー側 (`src/webtransport/h2/server.py`) は正しく発火する
- テストにもクライアント側の on_session_ready 発火を検証するものがない

## 設計方針

- connect() でイベントを消費せずにコールバック発火経路を確保するか、connect() が確立を検知した時点でコールバックを直接呼ぶ
- クライアント側の on_session_ready 発火を検証するテストを追加する

## 完了条件

- クライアントの `on_session_ready` が確立時に 1 回呼ばれる
- テストが追加される
