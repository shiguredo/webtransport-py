# CI の browser E2E ジョブに Playwright Firefox のインストールが無い

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-ci-install-playwright-firefox
- Reporter: @voluntas

## 目的

e2e-test ワークフローの `test_browser` ジョブが Firefox のブラウザバイナリを導入して
いないため、`tests/browser/test_webtransport_firefox.py` の 8 検証がすべて
「Executable doesn't exist at .../firefox-1538/...」でエラーになり、CI が失敗する。
Firefox の実ブラウザ E2E を CI で実行できる状態に戻す。

## 現状

- `.github/workflows/e2e-test.yml` の `Install Playwright browsers` ステップは
  `uv run playwright install chromium webkit` であり、Firefox を導入していない
- `tests/browser/test_webtransport_firefox.py` を追加した実行 (macos-26_arm64 / 3.14) では
  24 検証が通過し、Firefox の 8 検証が `BrowserType.launch` のエラーになった
- Playwright ブラウザのキャッシュキーは `${{ runner.os }}-playwright-${{ hashFiles('uv.lock') }}`
  であり、導入するブラウザの構成が変わってもキャッシュが作り直されない。キャッシュに
  Firefox が含まれないままヒットすると、インストールのたびに再ダウンロードになる

## 設計方針

- `Install Playwright browsers` に Firefox を追加する
- `Cache Playwright browsers` のキーに導入するブラウザの構成を含め、構成を変えたときに
  キャッシュを作り直せるようにする
- 変更対象: `.github/workflows/e2e-test.yml`

## 完了条件

- `test_browser` ジョブで Firefox を含む全ブラウザの E2E テストが実行され、通過する
- e2e-test ワークフローが緑に戻る

## 解決方法
