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
- [CHANGE] QUIC の `receive` / `send` / `create_client` / `accept` に実アドレスを必須化する
  - @voluntas
- [ADD] WebTransport over HTTP/2 を draft-ietf-webtrans-http2-15 に合わせて実装する
  - @voluntas
- [ADD] QUIC クライアントの証明書検証 (`ca_file` / カスタムコールバック) を実装する
  - @voluntas
- [ADD] QUIC の Session ticket と 0-RTT を実装する
  - @voluntas
- [ADD] QUIC の Connection Migration を実装する
  - @voluntas
- [ADD] QUIC の 0-RTT による early data 送受信を実装する
  - @voluntas
- [UPDATE] WebTransport over HTTP/3 と HTTP/3 の e2e テストを拡充する
  - @voluntas
- [UPDATE] CI の対応プラットフォームを Ubuntu 24.04 LTS / macOS 26 に揃える
  - @voluntas
- [FIX] QUIC の `send()` が輻輳ウィンドウ枯渇時に無限ループするのを修正する
  - @voluntas

### misc

- [FIX] CI の wheel テストで `pythonpath=["src"]` が拡張モジュール付き wheel を隠さないようにする
  - @voluntas
- [UPDATE] refs/ 配下の IETF draft を最新版に更新する
  - @voluntas
