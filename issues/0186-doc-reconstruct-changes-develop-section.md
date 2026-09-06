# CHANGES.md の ## develop セクションを shiguredo-changelog 規約に沿って初回リリース向けに再構成する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/doc-reconstruct-changes-develop-section
- Polished: {YYYY-MM-DD}

## 目的

`CHANGES.md` の `## develop` は 146 エントリを抱え、大半が develop 内で入れて直した中間状態の [FIX] (71 件)。shiguredo-changelog 規約は「派生元ブランチとの最終的な差分のみを記載」「中間状態の修正は記載しない」「エントリ 1 件 1 変更」「種別順序 CHANGE → ADD → UPDATE → FIX」「.rst / .md の変更は反映しない」を求める。派生元 `main` は Initial commit のみで、2026.1.0 の差分は「ライブラリ全体」に相当する。よって全 [FIX] は畳み [CHANGE] は [ADD] に統合するか削除する。加えて種別順序違反 / 矛盾ペア / 中間状態の入れ戻し / .md 更新の記載 / 記載漏れ複数が残存。

## 現状

- CHANGES.md の `## develop` エントリ 146 件: CHANGE 5 / ADD 34 / UPDATE 19 / FIX 71 + misc 17
- 派生元 `main` は Initial commit のみ (`git log --oneline main` で 1 コミット)、develop は 376 コミット先行
- PyPI に `2026.1.0.dev0`〜`dev14` と `dev16` が publish 済み。CHANGES 側は dev タグをリリースとして扱う節を持たない
- 規約違反エントリの具体:
  - `:172` [CHANGE] が [FIX] 群の中にあり順序違反
  - `:20`「bool 戻り値を廃止する」と `:172`「非 2xx 拒否時に False を返す」の矛盾ペア
  - `:230`/`:234`、`:232`/`:236` の「入れて戻した」中間状態
  - `:261` refs/ 配下の draft 更新、`:263` SKILL.md 最新化 (.md 変更で規約禁止)
  - `:245`「HTTP/2 クライアントの run() に is_closed() チェックを追加する」が `### misc` (機能変更)
  - `:3-10` 凡例順序 (CHANGE → UPDATE → ADD → FIX) が規約 (CHANGE → ADD → UPDATE → FIX) と不一致
- 記載内容と実装の食い違い: `:18` `send` の引数、`:68` `max_datagram_frame_size` の位置、他多数
- 実装との照合で存在するのに CHANGES に明記されていない機能 (SKILL.md 対応の変更等) が複数

## 設計方針

- shiguredo-changelog 規約に従い、`## develop` を約 30 件に再構成する。分類方針:
  - 残す: `[ADD]` の主要機能 (WT-H3 / WT-H2 / QUIC / HTTP2 / HTTP3 の実装追加、Config / Event / Session の主要 API 追加、`[ADD] 例外階層`、`[ADD] 配布 wheel に THIRD_PARTY_LICENSES.md を同梱する` 等)
  - 畳む: 個別 [ADD] を親機能に統合 (例: `[ADD] QUIC クライアントの background_recv`、`shutdown_stream` 等は `[ADD] QUIC asyncio Client (背景受信・recv_stream_data・shutdown_stream・...)` に集約)
  - 削る: 全 [FIX] 71 件 (中間状態)、依存バージョン更新 [UPDATE]、.md / refs/ 更新 [UPDATE]、`### misc` の内部整備
  - 書き直す: [CHANGE] 5 件を [ADD] に統合するか削除
- 種別順序と 1 エントリ 1 変更を徹底する
- 記載漏れの機能追加 (`drain_session` / `is_webtransport_ready` / `want_write` / `stop_sending` / `CapsuleType` / `terminate_session` / `set_local_window_size` / `submit_trailer` / `submit_push_promise` / `select_alpn` 等) が上記 [ADD] にすべて包摂されることを確認する
- リリース時に `## develop` を `## 2026.1.0` に変更し **リリース日** を記載する運用フロー (規約) を README のリリースビルド節に追記する
- dev タグ間の差分を残したい場合の運用は CODEBASE.md に例外規定として明記するかを別途検討する

## 完了条件

- `## develop` エントリが約 30 件に整理されていること
- 種別順序が CHANGE → ADD → UPDATE → FIX の順であること
- 矛盾ペア・入れ戻し・記載禁止の .md 更新が削除されていること
- 記載漏れの機能追加が全て親 [ADD] に包摂されていること
- README のリリース手順が更新されていること
- 既存のテスト全 822 件が引き続き通過すること
