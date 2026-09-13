# SKILL.md に載っている API と実装の API を機械的に照合するテストを追加する

- Created: 2026-09-13
- Completed: 2026-09-13
- Branch: feature/test-skill-api-consistency
- Polished: {YYYY-MM-DD}

## 目的

`skills/webtransport-py/SKILL.md` は利用者と LLM の双方が最初に参照する仕様書だが、実装が変わっても SKILL は誰も検証しないため、放置すると嘘の API を案内し続ける。issue 0185 の全面照合では 18 件の不一致が見つかり、その中には実装に存在しないメソッドの案内 (`h3.Client.on_stop_sending`) や、実装と逆の契約 (`connect()` が例外を送出するという記述) が含まれていた。人手のレビューだけでは同じ乖離が再発するため、機械的に検出する仕組みを入れる。

## 現状

- SKILL.md のクラス名・メソッド名・プロパティ名・モジュール関数名を実装と突き合わせるテストは存在しない (`tests/` にドキュメントを検証するテストが無い)
- issue 0185 の照合では、次の 2 種類を ast で突き合わせる一時スクリプトを書いて不一致 0 件まで持っていった
  - `<module>.<Class>.<member>` 形式の dotted 名の存在確認
  - フェンス付き python ブロックに並ぶシグネチャ行 (`def foo(...)` / `async def foo(...)` / `bar -> int`) のメソッド名を、直前の散文が示すクラスのメンバーと突き合わせる確認
- このスクリプトは一時ディレクトリに置いたままなので、リポジトリに残っていない

## 設計方針

- `tests/test_skill_api_consistency.py` を追加し、SKILL.md から API 参照を抽出して実装と突き合わせる
  - 実装側は `src/webtransport/webtransport_ext/*.pyi` (Sans I/O) と `src/webtransport/<module>/*.py` (asyncio ラッパー) を `ast` で解析して「クラス → メンバー名」「モジュール関数名」を集める
  - 対象モジュールは `quic` / `http3` / `h3` / `http2` / `h2` の 5 つ
- 判定は「SKILL に載っている名前が実装に無い」場合だけを失敗にする。実装にあって SKILL に無い API は対象外にする (SKILL は網羅を目的とした一覧ではない)
- 見るのは名前の存在だけにする。引数名・既定値・戻り値までは見ない (SKILL の表現揺れを許容し、メンテナンスコストを上げないため)
- モックやスタブは使わず、実ファイルを読むだけのテストにする
- シグネチャ一覧ブロックの所有者判定は、ブロック直前の散文にある `<module>.<Class>` を第一候補にし、ブロック内の `# <Class> のメソッド` コメントで上書きする (両方を実装する)

## 完了条件

- `tests/test_skill_api_consistency.py` が追加され、現状の SKILL.md で通過すること
- SKILL.md に存在しない API 名を 1 つ書くと失敗することを確認すること (テスト自体が機能することを mutation で確認する)
- 既存のテストが引き続き通過すること

## 解決方法

`tests/test_skill_api_consistency.py` を追加した。モックは使わず、SKILL.md と実装ファイルを読んで `ast` で解析するだけのテストにしている。

- `src/webtransport/webtransport_ext/*.pyi` (Sans I/O) と `src/webtransport/<module>/*.py` (asyncio ラッパー) を解析し、モジュールごとの「クラス名 → メンバー名」「モジュール直下の関数名」表を作る (`quic` / `http3` / `h3` / `http2` / `h2` の 5 モジュール)
- `test_dotted_api_names_exist`: SKILL.md の `<module>.<Class>.<member>` 形式の参照を抽出し、実装に存在するか検証する
- `test_signature_block_members_exist`: フェンス付き python ブロックのうちシグネチャ行 (`def` / `async def` / `プロパティ名 -> 型` / `Class.method(...)`) だけで構成されるものを抽出し、各メソッド名・プロパティ名が実装に存在するか検証する
  - ブロックの所有者クラスは、ブロック内の `# <Class> のメソッド` コメントを最優先し、無ければ直前の散文・見出しにある `mod.Class` から解決する。モジュール修飾が無いクラス名は一意に解決できるときだけ採用する
  - 散文が結線先など別クラスに言及している場合は、ブロック内の `Class.method(...)` 形式が示すクラスを優先する (`h3.Session` / `http3.Connection` / `h2.Session` の Sans I/O 一覧が該当)
  - 実行文を含むブロック (コード例) は対象外にする
- 判定は「SKILL に載っている名前が実装に無い」場合だけを失敗にし、実装にあって SKILL に無い API は対象外にした。引数名・既定値・戻り値は見ない
- 抽出そのものが壊れて検証が空振りしないよう、対象ブロック数が 10 未満なら失敗させている

検証:

- 現状の SKILL.md で 2 テストが通過することを確認した (シグネチャ一覧 14 ブロック / 253 名、dotted 名は全参照)
- mutation で検出できることを確認した
  - 散文の `quic.Connection.reset_stream()` を `reset_stream_typo()` に変えると dotted 側が該当行番号付きで失敗する
  - `h3.Client` の一覧の `send_datagram` を `send_datagram_typo` に変えるとシグネチャ側が失敗する
  - `h3.Session` の一覧の `receive_datagram` を `receive_datagram_typo` に変えるとシグネチャ側が失敗する (Sans I/O 一覧の所有者解決も機能している)
- `uv run pytest tests/ -q --timeout=60` で 1128 件すべて通過することを確認した
