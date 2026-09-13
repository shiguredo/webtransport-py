# SKILL.md に載っている API と実装の API を機械的に照合するテストを追加する

- Created: 2026-09-13
- Completed: {YYYY-MM-DD}
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

どのように対応するのかを明確にすること (例: どのようなコードを追加・修正するのか、どのようなテストを追加するのかなど)
