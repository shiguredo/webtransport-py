# WebTransport over HTTP/2 の未完成カプセルバッファに長さ上限を設ける

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-capsule-buffer-bound
- Polished: 2026-09-07

## 目的

WebTransport over HTTP/2 のサーバーで、ピアが Length を巨大にしたカプセルを送るとペイロードが揃うまで `capsule_buffer` に無制限蓄積する。HTTP/2 レベルは nghttp2 が自動 WINDOW_UPDATE を送るため受信は止まらず、リモートから発火可能なメモリ DoS 経路である。Length 解釈直後に全種別を対象とした上限検査を入れ、上限超過は WT_ERROR (プレースホルダ 0x52) でセッションを閉じる。自実装の送信は呼び出しごとに 1 カプセル化するため、カプセル単位の上限は正当な大容量転送を壊さない。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::process_capsules` は Length が揃うまで `wt_session->capsule_buffer` に加算する (上限なし)
- 実験手順 (Length = 2^30 の WT_STREAM ヘッダー + 64 MiB を 16 KiB DATA で注入。実行記録は未取得のため、実装時に再測定する)

## 設計方針

- `process_capsules` の Length 解釈直後に上限検査を入れる。対象は WT_STREAM / WT_STREAM_FIN に限らず DATAGRAM 等の全種別とする (型分岐前の蓄積のため。DATAGRAM は QUIC データグラムサイズ上限を超え得ないため、上限対象に含めて問題ない)
- 上限は `H2SessionConfig` に追加する (既定 1 MiB)。上限超過は WT_ERROR (プレースホルダ 0x52) でセッションを閉じる。単回 1 MiB 超の送信はアプリ側で分割する前提とし、分割しない送信は拒否される
- 受理前は 0157 規則 (64 KiB 上限・413 拒否) を適用し、確立後は本 issue 規則 (1 MiB 上限・WT_ERROR 切断) を適用する。両検査は排他的に適用し、二重検査しない
- 変更対象は `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h2.h` のみとする

## 完了条件

- Length = 2^30 のカプセルヘッダーを受信すると WT_ERROR でセッションが閉じること
- 正当な分割転送 (複数カプセルに分割されたストリーム) が影響を受けないこと
- `tests/` に長大 Length ヘッダー注入テストを追加すること
- 既存のテスト全 834 件が引き続き通過すること
