# 規約から外れている箇所を列挙して解消する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-fix-convention-violations
- Polished: 2026-09-15

## 目的

`AGENTS.md` と各スキルが定める規約から外れている箇所のうち、対象を列挙できるものをまとめて解消する。規約違反が残っていると、以後のレビューで同じ指摘が繰り返し上がり、本当に見るべき差分が埋もれる。対象範囲は下記に列挙したものに限る。

## 現状

コメントの言語 (`AGENTS.md` の「コメントは全て日本語にすること」):

- `src/bindings/` の各ファイルに、日本語を含まない説明コメントと列挙ラベルが残っている
  - バインディング登録部のラベル: `src/bindings/quic.cpp` の `// QuicConfig`、`src/bindings/webtransport_h2.cpp` の `// CapsuleType` など
  - プロトコル処理部の説明: `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_reset_stream` にある `// Stream ID` / `// Error Code` / `// Reliable Size`、`src/bindings/http3.cpp` の `Http3Connection::get_required_streams` にある `// qpack encoder`、`src/bindings/webtransport_h2.cpp` の `// Bit 0: initiator (0 = client, 1 = server)` など
  - ヘッダーの説明: `src/bindings/quic.h` の `// ALPN (Application-Layer Protocol Negotiation)`、`src/bindings/webtransport_h2.h` の数件など
- 一方、次のコメントは日本語化できないため対象外とする。`}  // namespace ...` と `#endif  // ...` の閉じラベル (clang-format が生成し、日本語化しても元に戻る)、仕様の英文引用、節参照のみのコメント (`// draft-15 Section 4.3.1`)、コードポイント名のみのコメント (`// H3_FRAME_UNEXPECTED`)
- `src/bindings/quic.cpp` の `QuicConnection::reset_stream` に依存ライブラリの内部名だけを書いたコメント `// (streamfrq) が無ければ、リセット送出パケットにデータは同梱されない` がある。`streamfrq` は ngtcp2 の実在フィールド (`ngtcp2_strm::tx.streamfrq`、再送用に保持する送信済み未 ACK の STREAM フレームキュー) だが、リポジトリ内からは辿れない (`_deps/` は追跡外)

テストメッセージの言語 (`AGENTS.md` の「テストのログメッセージは全て日本語にすること」):

- `tests/` 配下のメッセージ付き表明 234 件のうち、日本語を含まないものが 2 件ある
  - `tests/prop_quic_handshake.py` の `"Handshake should complete"`
  - `tests/test_e2e_http2.py` の `f"large response took {elapsed:.2f}s"`

issue 番号の混入 (`shiguredo-issues` の「issue 番号や issue への言及を書いてはいけない場所」):

- 次のテストの docstring とコメントに issue 番号が残っている
  - `tests/prop_h2_stateful.py` (「既知バグ (0156 / 0157 / 0158 / 0159) の回帰ピンとして」「残クレジットが窓を超えて負にならない (0156 の回帰ピン)」)
  - `tests/prop_h3_stateful.py` (「そのため issue 0145 の」「(0145 の回帰ピン)」「issue 0145 のクラッシュ経路は…」)
  - `tests/prop_quic_stateful.py` (「issue 0146 (Config 値で assert に到達する経路) の回帰ピンとして」「(0146 の回帰ピン)」「issue 0146 は…」)
  - `tests/test_quic_stream_control.py` (「ピア側の ping_recv (0014 の接続統計) が増加する」)
  - `tests/test_webtransport_h3_reject_session.py` (「0104 の h2 側と同じ)」)
  - `tests/test_e2e_webtransport_h3.py` (「issue の完了条件が例示する」)。番号は無いが issue への言及であり、参照先も特定できないため、何を前提にしているかを書く形に直す
- `src/` と `examples/` には issue 番号の参照は無い

`assert` の本番利用 (`shiguredo-python` の「`assert` を本番の不変条件チェックに使わないこと」):

- `src/webtransport/http2/server.py` の `Server._handle_client` に `assert read_task is not None` がある
- `src/webtransport/http3/server.py` の `Server._drain_quic_events` に `assert client.quic_connection is not None` と `assert client.http3_connection is not None` がある
- src 配下で本番の `assert` を使っているのはこの 3 箇所のみである

依存バージョンの指定 (`shiguredo-python` の「上限なしの `>=` 単独指定は禁止」):

- `pyproject.toml` の `[build-system].requires` と `[dependency-groups].build` が `scikit-build-core>=1.0.3` を上限なしで指定している。`uv.lock` にも同じ指定子が記録されている

prek の実行順 (`shiguredo-python` の「`priority` で `ruff-format` → `ruff-check` → `ty` → `pytest` の順に実行すること`):

- `prek.toml` の ty フックに `priority` が無い。prek は未指定時にフック定義順の index を暗黙の priority にするため、ty の暗黙値は `ruff-check` (10) より後・pytest (30) より前になり、実行順は規約どおりである。ただし順序が設定に現れておらず、フックを増減すると変わり得る

コミットメッセージ (`shiguredo-git` の「日本語で書くこと」「命令形「〜する」の形で書くこと」):

- `dev.py` のリリース用バージョン更新処理が `[canary] Bump version to {version}` という英語のメッセージでコミットする
- `[canary]` は時雨堂の複数リポジトリ共通の canary コミットの識別子であり、本リポジトリにもそれを解釈するコードは無い。識別子として残し、本文だけを日本語の命令形にする

## 設計方針

- いずれも挙動を変えない。列挙した対象は規約が方針を定めているため、言い換えの判断で迷わない
- 英語コメントは、英語の説明文と列挙ラベルを日本語に書き換える。`}  // namespace ...` と `#endif  // ...` の閉じラベル、仕様の英文引用、節参照のみのコメント、コードポイント名のみのコメントは対象外とする
- `streamfrq` のコメントは、ngtcp2 の再送待ち送信フレームキューを指すことを日本語で補足する (内部名だけを残さない)
- `assert` の置き換えは、呼び出し側で検査済みの型絞り込みであるため `if ...: raise RuntimeError(...)` にする
- issue 番号は削除し、代わりに「何が壊れると落ちるか」を書く。削除の際は closed の該当 issue を読み、回帰ピンとしての意図が失われないようにする
- `tests/prop_h2_stateful.py` は issue 番号の削除のみを行い、invariant の本体と docstring の整合は別 issue (0227) が担当する
- `priority` の意味を補うコメントは `prek.toml` に既にあるため追記しない。ty には `priority = 20` を明示して順序を設定に固定する

## 完了条件

- 列挙した英語コメント (対象外を除く) が日本語になっている
- `streamfrq` のコメントが、ngtcp2 が再送用に保持する送信済み未 ACK の STREAM フレームキューを指すことを説明している
- `tests/` 配下の表明メッセージ (2 件) が日本語になっている
- 列挙した 6 ファイルに issue 番号の参照が残っていない
- `src/` の本番 `assert` 3 箇所が `if` + `raise RuntimeError` になっている
- `pyproject.toml` の 2 箇所が `scikit-build-core~=1.0.3` になり、`uv.lock` が再生成されている
- `prek.toml` の ty フックに `priority = 20` がある
- `dev.py` のコミットメッセージが `[canary] バージョンを {new_version} に上げる` の形になり、ドライラン出力が実際のメッセージと一致する
- `ruff` / `ty` / `pytest` と prek の全フックが通過する

## 解決方法

- `src/bindings/` の各ファイルの英語の説明コメントと列挙ラベルを日本語化する (対象外の閉じラベル・英文引用・節参照・コードポイント名は触らない)
- `src/bindings/quic.cpp` の `QuicConnection::reset_stream` の `streamfrq` のコメントを、ngtcp2 の再送待ち送信フレームキューを指す旨に書き換える
- `tests/prop_quic_handshake.py` と `tests/test_e2e_http2.py` のメッセージを日本語にする
- 列挙した 6 ファイルから issue 番号を削除し、回帰ピンの意図を「何が壊れると落ちるか」で書き直す
- `src/webtransport/http2/server.py` の `Server._handle_client` と `src/webtransport/http3/server.py` の `Server._drain_quic_events` の `assert` を `if` + `raise RuntimeError` に置き換える
- `pyproject.toml` の 2 箇所を `scikit-build-core~=1.0.3` にし、`uv lock` で `uv.lock` を再生成して同じコミットに含める
- `prek.toml` の ty フックに `priority = 20` を追加する
- `dev.py` のコミットメッセージを `[canary] バージョンを {new_version} に上げる` にし、ドライラン出力の文字列も同じメッセージを印字するように直す
