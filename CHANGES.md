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

- [CHANGE] Windows 対応を終了する
  - @voluntas
- [ADD] WebTransport over HTTP/2 を draft-ietf-webtrans-http2-15 に合わせて実装する
  - @voluntas
- [UPDATE] WebTransport over HTTP/3 と HTTP/3 の e2e テストを拡充する
  - @voluntas
- [UPDATE] CI の対応プラットフォームを Ubuntu 24.04 LTS / macOS 26 に揃える
  - @voluntas

### misc

- [FIX] CI の wheel テストで `pythonpath=["src"]` が拡張モジュール付き wheel を隠さないようにする
  - @voluntas
- [UPDATE] refs/ 配下の IETF draft を最新版に更新する
  - @voluntas
