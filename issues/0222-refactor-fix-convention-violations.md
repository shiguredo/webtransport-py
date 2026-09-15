# 機械的に是正できる規約違反を解消する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-fix-convention-violations
- Polished: {YYYY-MM-DD}

## 目的

`AGENTS.md` と各スキルが定める規約から外れている箇所のうち、判断を伴わず機械的に直せるものをまとめて解消する。規約違反が残っていると、以後のレビューで同じ指摘が繰り返し上がり、本当に見るべき差分が埋もれる。

## 現状

コメントの言語 (`AGENTS.md` の「コメントは全て日本語にすること」):

- `src/bindings/quic.cpp` / `http2.cpp` / `http3.cpp` / `webtransport_h2.cpp` / `webtransport_h3.cpp` のバインディング登録部に、日本語を含まない英語コメントが残っている (例: `// QuicConfig`、`// Stream ID`、`// Error Code`、`// Reliable Size`、`// CapsuleType`、`// qpack encoder`)
- `src/bindings/quic.h` の `// ALPN (Application-Layer Protocol Negotiation)` など、ヘッダー側にも数件ある
- `src/bindings/quic.cpp` の `H3Session` リセット関連コメントに、コードにも依存ライブラリにも存在しない語 (`streamfrq`) が残っている

テストメッセージの言語 (`AGENTS.md` の「テストのログメッセージは全て日本語にすること」):

- `tests/prop_quic_handshake.py` のハンドシェイク完了を確認する表明に、唯一の英語メッセージ (`"Handshake should complete"`) が残っている。tests 配下の全表明を機械的に確認したところ、日本語を含まないメッセージはこの 1 件のみである

issue 番号の混入 (`shiguredo-issues` の「issue 番号や issue への言及を書いてはいけない場所」):

- `tests/prop_h2_stateful.py` / `tests/prop_h3_stateful.py` / `tests/prop_quic_stateful.py` の docstring とコメントに issue 番号が残っている

`assert` の本番利用 (`shiguredo-python` の「`assert` を本番の不変条件チェックに使わないこと」):

- `src/webtransport/http2/server.py` の `Server._handle_client` に `assert read_task is not None` がある
- `src/webtransport/http3/server.py` の `Server.setup_http3_streams` に `assert client.quic_connection is not None` と `assert client.http3_connection is not None` がある
- src 配下で本番の `assert` を使っているのはこの 3 箇所のみである

依存バージョンの指定 (`shiguredo-python` の「上限なしの `>=` 単独指定は禁止」):

- `pyproject.toml` の `[build-system].requires` と `[dependency-groups].build` が `scikit-build-core>=1.0.3` を上限なしで指定している

prek の実行順 (`shiguredo-python` の「`priority` で `ruff-format` → `ruff-check` → `ty` → `pytest` の順に実行すること」):

- `prek.toml` の ty フックに `priority` が無く、既定値になって `ruff-format` と同じ段で並列実行される。`ruff-check` は 10、pytest は 30 を指定済みである

コミットメッセージ (`shiguredo-git` の「日本語で書くこと」「命令形「〜する」の形で書くこと」「prefix を付けないこと」):

- `dev.py` のリリース用バージョン更新処理が `[canary] Bump version to {version}` という英語の prefix 付きメッセージでコミットする

## 設計方針

- いずれも挙動を変えない。判断が必要な項目 (どの語に言い換えるか等) はあるが、方針は規約が定めているため迷わない
- `assert` の置き換えは、呼び出し側で検査済みの型絞り込みであるため `if ...: raise RuntimeError(...)` にする
- 英語コメントは日本語に書き換える。仕様の英文引用は引用として残してよい
- issue 番号は削除し、代わりに「何が壊れると落ちるか」を書く
- スキルの同梱設定 (`prek.toml`) を参考に、`priority` の意味を 1 行コメントで補う

## 完了条件

- 上記すべての規約違反が解消される
- `ruff` / `ty` / `pytest` と prek の全フックが通過する
- `dev.py` の変更はドライランの出力で確認できる

## 解決方法

- 各 C++ ファイルの英語コメントを日本語化する
- `tests/prop_quic_handshake.py` のメッセージを日本語にする
- `tests/prop_h2_stateful.py` / `prop_h3_stateful.py` / `prop_quic_stateful.py` から issue 番号を削除する
- `src/webtransport/http2/server.py` / `http3/server.py` の `assert` を `if` + `raise` に置き換える
- `pyproject.toml` の 2 箇所を `scikit-build-core~=1.0.3` にする
- `prek.toml` の ty フックに `priority = 20` を追加する
- `dev.py` のコミットメッセージを日本語の命令形にする
