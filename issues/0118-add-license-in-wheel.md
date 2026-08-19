# 配布 wheel にライセンス文書を同梱する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/add-license-in-wheel
- Polished: {YYYY-MM-DD}

## 目的

ビルドした wheel にライセンス文書 (LICENSE / THIRD_PARTY_LICENSES.md) が含まれておらず、静的リンクする MIT ライブラリ (ngtcp2 / nghttp3 / nghttp2 / AWS-LC) の著作権表示義務を満たしていない問題を修正する。配布物にライセンス文書を同梱する。

## 現状

- `pyproject.toml` の `license-files = ["LICENSE"]` は設定されているが、実際の wheel (dist/ 配下で展開確認済み) には LICENSE / THIRD_PARTY_LICENSES.md が同梱されていない (dist-info に METADATA / WHEEL のみ)
- `THIRD_PARTY_LICENSES.md` はリポジトリに存在するが、`sdist.include` (pyproject.toml) にも wheel パッケージ構成にも含まれない
- sdist には LICENSE / THIRD_PARTY_LICENSES.md が含まれるため、wheel 側の設定だけが欠落している

## 設計方針

- wheel に LICENSE と THIRD_PARTY_LICENSES.md が含まれるようにビルド設定を修正する (license-files の設定見直し、または wheel パッケージへの明示的追加)
- ビルド後の wheel 展開で同梱を確認する

## 完了条件

- ビルドした wheel に LICENSE と THIRD_PARTY_LICENSES.md が含まれる
