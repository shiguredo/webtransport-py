# THIRD_PARTY_LICENSES.md に nanobind のライセンスを追記する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/doc-add-nanobind-license-to-third-party-licenses
- Polished: {YYYY-MM-DD}

## 目的

`nanobind_add_module` は既定で `NB_STATIC` のため、nanobind のランタイム (BSD-3-Clause) と tsl::robin_map (MIT) が `webtransport_ext.so` に静的リンクされ配布される。BSD-3-Clause はバイナリ配布時の通知再掲を条件とするが、`THIRD_PARTY_LICENSES.md` の見出しは ngtcp2 / nghttp3 / nghttp2 / AWS-LC の 4 つのみで nanobind と tsl::robin_map の記載が無い。README の第三者ライセンス節 (`README.md`) も同じ 4 つ。ライセンス条件違反であり配布物の欠陥。

## 現状

- `THIRD_PARTY_LICENSES.md` の見出し: `## ngtcp2` / `## nghttp3` / `## nghttp2` / `## AWS-LC` の 4 つのみ (nanobind の grep 結果 0 件)
- `README.md` の「第三者ライセンス」節も「ngtcp2 / nghttp3 / nghttp2 / AWS-LC を静的リンクしています」で nanobind の記載無し
- `CMakeLists.txt` の `nanobind_add_module(webtransport_ext ... FREE_THREADED ...)` は `NB_STATIC` 既定のため nanobind ランタイムを `.so` に静的リンクする
- nanobind のライセンス: BSD-3-Clause (`.venv/lib/python*/site-packages/nanobind-*.dist-info/METADATA` の `License :: OSI Approved :: BSD License`)
- nanobind が内包する `tsl::robin_map` は MIT
- `pyproject.toml:12` `license-files = ["LICENSE", "THIRD_PARTY_LICENSES.md"]` で `THIRD_PARTY_LICENSES.md` を wheel に同梱するため、追記した内容が wheel に反映される

## 設計方針

- `THIRD_PARTY_LICENSES.md` に `## nanobind` セクションを追加し、BSD-3-Clause の全文を転記する
- `## tsl::robin_map` セクションを追加し、MIT の全文を転記する (nanobind の依存)
- `README.md` の「第三者ライセンス」節を「ngtcp2 / nghttp3 / nghttp2 / AWS-LC / nanobind (tsl::robin_map を含む) を静的リンクしています」に更新する
- AWS-LC が内包する third-party (fiat-crypto / s2n-bignum / jitterentropy 等) はビルド時のみ / crypto 内包で網羅済みかを再確認する (既存の `## AWS-LC` 節が全文転記済みならそのまま)
- `THIRD_PARTY_LICENSES.md` の凡例に「アルファベット順」や「更新手順」を明記するかは検討する

## 完了条件

- `THIRD_PARTY_LICENSES.md` に nanobind と tsl::robin_map のライセンスが追加されていること
- wheel に含まれる `webtransport_py-*.dist-info/licenses/THIRD_PARTY_LICENSES.md` に上記が反映されていること
- `README.md` の第三者ライセンス節が更新されていること
- 既存のテスト全 822 件が引き続き通過すること
