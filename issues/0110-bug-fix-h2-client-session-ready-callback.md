# h2.Client の on_session_ready コールバックが発火しない問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-client-session-ready-callback
- Polished: 2026-08-24

## 目的

高レベル `h2.Client` で `on_session_ready` コールバックが一度も呼ばれない問題を修正する。connect() が SESSION_READY イベントを消費してしまうため、公開コールバックが機能していない。

## 現状

- `src/webtransport/h2/client.py` の `Client.connect` は 200 OK 待ちループで `next_event()` を取り出し、SESSION_READY を検知した時点で `_connected = True; return True` する (イベントを消費)
- 同じ `Client.run` のイベントループが `_on_session_ready` を呼ぶのは SESSION_READY イベント受信時だが、connect() が常に消費済みのため、単一セッション利用ではコールバックが一度も呼ばれない (SESSION_READY は bindings が 200 応答受信時に 1 回のみ push する)
- サーバー側 (`src/webtransport/h2/server.py`) はイベントループ内で発火するため正しく機能する。h3.Client も run() 内 (イベント処理) で発火する構造であり、パッケージ内の方式は「run() のイベントループで発火」で統一されている
- テストにもクライアント側の on_session_ready 発火を検証するものがない

## 設計方針

- **connect() はイベントを消費しつつ、消費した SESSION_READY を未配信バッファへ引き継ぐ**: connect() の判定 (0111 の成果: 非 2xx 拒否で False を返す等) を壊さないため、イベントを pop して判定に使うのは現行どおりとする。ただし pop した SESSION_READY は `self._pending_events` に保存する
- **run() はイベントループ開始時に未配信バッファを先に処理する** (既存の SESSION_READY 分岐を経由して `_on_session_ready` を発火させる)。これにより connect() (または __aenter__) と登録順序に依存せず、run() 実行時に発火する (with 構文の __aenter__ が connect() を先に実行するケースでも成立する)
- 発火は 1 回のみ (バッファの引き継ぎは 1 回きり。run() が再利用されるケースは想定しない)
- 変更対象: `src/webtransport/h2/client.py` (connect / run / バッファ) / テスト / CHANGES.md (## develop への [FIX])

## 完了条件

- connect() (または __aenter__) 後に run() を実行すると、コールバックが 1 回だけ発火する
- コールバック登録の順序 (connect() の前後) によらず発火する
- 拒否 (非 2xx) 時は connect() が False を返す挙動が維持される
- CHANGES.md の `## develop` に [FIX] エントリを追加する
- テストが追加され、全テストが通る
