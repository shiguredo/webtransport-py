# skills/webtransport-py/SKILL.md の WebTransport over HTTP/3 の記述を最新化する

- Created: 2026-08-02
- Completed: YYYY-MM-DD
- Branch: feature/update-webtransport-skill
- Polished: {YYYY-MM-DD}

## 目的

`skills/webtransport-py/SKILL.md` の WebTransport over HTTP/3 の記述を現在の実装と一致させる。スキルドキュメントが古いと、LLM がライブラリを使う際に存在しない API や引数を参照してしまう。

## 現状

`skills/webtransport-py/SKILL.md` の h3 セクションが最新 API を反映していない:

- `h3.Server.__init__` のシグネチャに `allowed_origins` が無い (`src/webtransport/h3/server.py` の `Server` には実装済み)
- `h3.Server` の主なメソッド一覧に `open_stream` と `close_stream` が無い (同じく実装済み)
- `h3.Client.__init__` のシグネチャに `origin` が無い (`src/webtransport/h3/client.py` の `Client` には実装済み)

## 設計方針

- `skills/webtransport-py/SKILL.md` の h3 セクションのシグネチャとメソッド一覧を、`src/webtransport/h3/server.py` と `src/webtransport/h3/client.py` の現在の実装に合わせて更新する
- あわせて同ファイル内で最新化が必要な他レイヤーの記述があれば確認する

## 完了条件

- `skills/webtransport-py/SKILL.md` の h3 セクションが現在の実装と一致する
- シグネチャ・メソッド一覧に記載された API がすべて実在する
