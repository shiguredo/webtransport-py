# 変更履歴

- CHANGE
  - 後方互換性のない変更
- UPDATE
  - 後方互換性がある変更
- ADD
  - 後方互換性がある追加
- FIX
  - バグ修正

## develop

- [ADD] WebTransport over HTTP/2 を draft-ietf-webtrans-http2-15 に合わせて実装する
  - @voluntas
- [UPDATE] WebTransport over HTTP/3 と HTTP/3 の e2e テストを拡充する
  - @voluntas
- [UPDATE] CI の対応プラットフォームを macOS 26 / Ubuntu 26.04・24.04 / Windows 2025・11 に揃える
  - @voluntas

### misc

- [FIX] Windows 11 ARM64 でネイティブ aarch64 Python と ClangCL を使い AWS-LC をビルドできるようにする
  - @voluntas
- [FIX] CI の wheel テストで `pythonpath=["src"]` が拡張モジュール付き wheel を隠さないようにする
  - @voluntas
- [FIX] auditwheel が manylinux タグを付けられない場合は linux タグのまま wheel を出す
  - @voluntas
- [UPDATE] refs/ 配下の IETF draft を最新版に更新する
  - @voluntas
