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
- [ADD] WebTransport over HTTP/3 クライアントに Origin ヘッダー送信機能を追加する
  - @voluntas
- [ADD] WebTransport over HTTP/3 サーバーの Origin ヘッダー検証を実装する
  - @voluntas
- [ADD] WebTransport over HTTP/3 サーバーにストリームを開く API を追加する
  - @voluntas
- [ADD] WebTransport over HTTP/3 と HTTP/3 のストリーム状態確認 API を公開する
  - @voluntas
- [ADD] HTTP/3 の送信側拡張 API (トレーラ / 1xx レスポンス / graceful shutdown の開始通知 / 書き込み側シャットダウン) を追加する
  - @voluntas
- [ADD] HTTP/3 の優先度制御 API (RFC 9218) と Priority ヘッダー値のパースを追加する
  - @voluntas
- [ADD] QUIC の接続統計 API (RTT / 輻輳ウィンドウ / フロー制御残量 / 送受信量等) を公開する
  - @voluntas
- [ADD] QUIC の接続状態・エラー・ピア情報 API (コネクションエラー / TLS エラー / トランスポートパラメータ / バージョン / 接続 ID 等) を公開する
  - @voluntas
- [ADD] HTTP/2 のセッション状態確認 API (SETTINGS / ウィンドウサイズ / 送信キュー / half-closed 状態等) を公開する
  - @voluntas
- [ADD] HTTP/2 のセッション制御 API (GOAWAY による即時終了 / ローカルウィンドウサイズの動的変更) を公開する
  - @voluntas
- [ADD] HTTP/2 のメッセージング拡張 API (トレーラ送信 / RFC 9218 の優先度制御 / Server Push / ALPN 選択) を公開する。`Http2Config` に `no_rfc7540_priorities` (デフォルト true) を追加し、SETTINGS に NO_RFC7540_PRIORITIES を含める
  - @voluntas
- [UPDATE] WebTransport over HTTP/3 と HTTP/3 の e2e テストを拡充する
  - @voluntas
- [UPDATE] CI の対応プラットフォームを Ubuntu 24.04 LTS / macOS 26 に揃える
  - @voluntas
- [FIX] QUIC の `send()` が輻輳ウィンドウ枯渇時に無限ループするのを修正する
  - @voluntas
- [FIX] WebTransport over HTTP/3 サーバーの STREAM_RESET イベントで誤ったセッション ID が渡されるのを修正する
  - @voluntas
- [FIX] WebTransport over HTTP/3 のリセット・セッション終了時に送信バッファが解放されないのを修正する
  - @voluntas
- [FIX] QUIC サーバーの RETRY 送出要求時に接続が閉じられた状態にならない問題を修正する
  - @voluntas
- [FIX] close() が生成した CONNECTION_CLOSE パケットを送出しない問題を修正する
  - @voluntas

### misc

- [FIX] CI の wheel テストで `pythonpath=["src"]` が拡張モジュール付き wheel を隠さないようにする
  - @voluntas
- [UPDATE] refs/ 配下の IETF draft を最新版に更新する
  - @voluntas
