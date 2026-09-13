# WebTransport over HTTP/2 の受信にアプリ消費連動の背圧を導入する

- Created: 2026-09-07
- Completed: 2026-09-13
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

## 解決方法

- `H2Session::initialize` で `nghttp2_option_set_no_auto_window_update` を有効にし、受信ウィンドウの自動更新を止めた。`nghttp2_session_client_new2` / `nghttp2_session_server_new2` で option を渡している
- `on_data_chunk_recv_callback` でストリームごとの受信バイト数を記録し、カプセルを解析してアプリ向けイベント (`STREAM_DATA` / `DATAGRAM` / エラー等) を組み立てた時点で `nghttp2_session_consume` を呼ぶ。WINDOW_UPDATE は `receive()` 末尾の `nghttp2_session_send` が送出する
- 完成したカプセルの消費だけでは「1 カプセルが受信ウィンドウより大きい」場合にデッドロックする (ウィンドウが戻らないので残りが届かず、カプセルが完成しない)。未完成バイトが閾値 (`max(32768, ピアの最大フレームサイズ)`) を超えたら超過分を消費してウィンドウを開けることで解消した。この閾値はアプリ未消費時に保持する未完成バイト数の上限にもなる
- テスト専用の `_test_unfinished_capsule_bytes` を追加し、未完成カプセルの保持バイト数が有界であること (256 KiB を送っても 64 KiB 以下) と、デッドロックしないこと (ウィンドウより大きいカプセルが最後まで届くこと) を白箱で検証する
- 受信ウィンドウがアプリの消費に連動するため、既存テストのうち 1 回の転送がコネクション初期ウィンドウ (65535) を超えるものは、送るたびに対向へ WINDOW_UPDATE を届ける形に直した (`test_boundary_payload_accepted` は上限を 32 KiB にして初期ウィンドウ内で検証、`test_session_transfer_beyond_1mib` / `test_single_stream_transfer_beyond_256kib` は HTTP/2 側のウィンドウを広げて WT 層のクレジット補充の検証に集中)
- 全 1116 テストが通ることを確認した (未完成カプセルの消費を外すと 3 テストが失敗することも確認済み)
