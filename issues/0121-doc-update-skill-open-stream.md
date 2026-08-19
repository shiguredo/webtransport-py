# skills/webtransport-py/SKILL.md の h3.Client.open_stream 記述を現実装に合わせて更新する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/update-skill-open-stream
- Polished: {YYYY-MM-DD}

## 目的

`skills/webtransport-py/SKILL.md` の `h3.Client.open_stream` の記述が、失敗時の挙動が変更された後の現実装と食い違っている。LLM が古い挙動を前提にコードを書くのを防ぐため、記述を最新化する。

## 現状

- `skills/webtransport-py/SKILL.md` の h3.Client.open_stream の記述は「Sans I/O の h3.Session.open_stream の登録に失敗しても stream_id を返す (失敗を無視する実装上の非対称。サーバー側の open_stream は失敗時 -1 を返す)」という内容
- 実際の実装 (`src/webtransport/h3/client.py` の `Client.open_stream`) は登録失敗時に RESET_STREAM を送って -1 を返す (CHANGES.md の「クライアントの open_stream が失敗時に無効な stream_id を返す問題を修正する」で変更済み)
- 記述が修正前の挙動のまま残っている

## 設計方針

- SKILL.md の h3.Client.open_stream の記述を現実装 (失敗時 -1 返却) に合わせて更新する
- あわせて SKILL.md の他の記述に現実装とのドリフトがないか軽く確認する (大きな調査は対象外)

## 完了条件

- SKILL.md の h3.Client.open_stream の記述が現実装と一致する
