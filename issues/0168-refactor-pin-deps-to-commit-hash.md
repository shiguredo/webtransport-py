# deps.json の ngtcp2 / nghttp3 をブランチ参照からコミットハッシュ (ref) 指定に変えて wheel の再現性を確保する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-pin-deps-to-commit-hash
- Polished: {YYYY-MM-DD}

## 目的

`deps.json` の ngtcp2 (reliable-stream-reset ブランチ) と nghttp3 (webtransport ブランチ) がブランチ参照になっており、wheel ビルドが非再現。上流のブランチ HEAD が進んでもキャッシュ済み `_deps/<name>/<branch>/` ディレクトリが再利用され続けるため、CI の `actions/cache` の `restore-keys` 前方一致で古い `_deps` が復元されると、上流の更新が反映されない。正式リリースに向けて再現ビルドを確保するため `ref` (コミットハッシュ) 指定に変える。`parse_deps_git_ref` は既に `ref` 指定に対応済み。

## 現状

- `deps.json` の ngtcp2: `"branch": "reliable-stream-reset"`
- `deps.json` の nghttp3: `"branch": "webtransport"`
- `deps.json` の nghttp2: `"tag": "v1.70.0"` (固定済み)、aws-lc: `"tag": "v5.8.0"` (固定済み)
- `CMakeLists.txt` の `parse_deps_git_ref` は `tag` → `branch` → `ref` の順で解決し、`ref` のとき `USE_SHALLOW FALSE`、`OUT_VERSION` = `GIT_TAG` (ハッシュ) となる
- ハッシュ指定なら `_deps/<name>/<hash>/` がディレクトリ名になり、パスが内容を一意に表す
- `.github/workflows/wheel.yml` / `e2e-test.yml` の `actions/cache` は `restore-keys: ${{ runner.os }}-deps-${{ matrix.platform.name }}-` で前方一致復元
- `hashFiles('deps.json', ...)` はブランチ HEAD の更新を検知できない
- CHANGES.md の `[UPDATE] nghttp3 の webtransport ブランチを最新化する` に対応する差分がリポジトリ内に無い (deps.json は変わらない)
- 現時点の上流 HEAD (実測): ngtcp2 `reliable-stream-reset` = `1e4399d428ddcf510eca55b0f11306e333aa5194` (2026-07-28)、nghttp3 `webtransport` = `ffc6cdb93eb3ddb9a8f10abbd032255c5f320e1c` (2026-08-18)

## 設計方針

- `deps.json` の ngtcp2 と nghttp3 を `"ref": "<40 文字ハッシュ>"` 指定に変える
- 上記の実測ハッシュを初期値に採用する (ngtcp2: `1e4399d428ddcf510eca55b0f11306e333aa5194`、nghttp3: `ffc6cdb93eb3ddb9a8f10abbd032255c5f320e1c`)
- `parse_deps_git_ref` は変更不要 (既に対応済み)
- CI キャッシュキーが `hashFiles('deps.json')` を含むため、`ref` を変えるだけでキャッシュキーが更新される
- `_deps` cache の肥大化 (1.3 GB の aws-lc source が版ごとに蓄積) は別 issue で対応する (source を cache 対象から外し install / build のみ保存する)
- README / SKILL.md に「依存は特定コミットに固定」を明記する
- 上流ブランチが進んだ際は本ファイルを更新するプロセス (`update-deps` スキル相当) を維持する

## 完了条件

- `deps.json` が ngtcp2 / nghttp3 とも `ref` 指定になること
- fresh clone から `make wheel` で同じバイナリが再現できること
- CI キャッシュが `ref` 更新時に確実に無効化されること
- 既存のテスト全 822 件が引き続き通過すること
