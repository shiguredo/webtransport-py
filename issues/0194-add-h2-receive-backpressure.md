# WebTransport over HTTP/2 の受信にアプリ消費連動の背圧を導入する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h2-receive-backpressure
- Polished: {YYYY-MM-DD}

## 目的

HTTP/2 レベルは nghttp2 が既定で自動 WINDOW_UPDATE を送るため、アプリの消費速度と無関係に受信ウィンドウが開き続ける。`nghttp2_option_set_no_auto_window_update` を有効にし、アプリ消費に連動した背圧を導入する。issue 0158 から分離した振る舞い追加であり、検出型防御とは独立に検証する。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::initialize` は `nghttp2_option` を生成せず、自動 WINDOW_UPDATE が既定有効である
- `nghttp2_session_consume` の呼び出しは無い

## 設計方針

- `initialize` で `nghttp2_option_set_no_auto_window_update` を有効にする
- 受理後のバイトはアプリ消費時に `nghttp2_session_consume` する。受理前の楽観的バイトは到着時に consume する (issue 0157 の楽観バッファ到達を優先するため)。413 拒否時は追加処理なしとする
- 変更対象は `src/bindings/webtransport_h2.cpp` のみとし、Python 高レベル層の変更は行わない

## 完了条件

- アプリ未消費時に受信ウィンドウが開き続けないこと
- スループットの著しい悪化やデッドロックがないこと
- `tests/` に背圧とスループット回帰のテストを追加すること
- 既存のテスト全 834 件が引き続き通過すること
