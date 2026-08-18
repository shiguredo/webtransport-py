# WebTransport over HTTP/2 のフロー制御カプセルの受信値検証 (減少値・2^60 上限) を実装する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-flow-control-capsule-decrease
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http2-15 Section 6.5 / 6.6 / 6.7 の MUST「前回受信値より小さい WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS を受信したら WT_FLOW_CONTROL_ERROR でセッションを閉じる」と、Section 6.7 / 6.10 の MUST「Maximum Streams が 2^60 を超える値は WT_FLOW_CONTROL_ERROR」を実装する。現状は減少値・2^60 超過を受信しても黙って無視し、仕様違反のピアを検知できない。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_max_data` / `handle_wt_max_stream_data` / `handle_wt_max_streams` はいずれも `max()` を取るだけで、減少値の検知が無い
- Section 6.7 (WT_MAX_STREAMS) / 6.10 (WT_STREAMS_BLOCKED) の「Maximum Streams が 2^60 を超える値は WT_FLOW_CONTROL_ERROR」も未実装。WT_STREAMS_BLOCKED (0x190B4D43 / 0x190B4D44) は `process_capsule` でペイロード未解析のまま黙殺されている
- これらカプセルの受信を検証するテストが存在しない

## 設計方針

- **対象スコープ**:
  - 減少値検知: WT_MAX_DATA (6.5) / WT_MAX_STREAM_DATA (6.6) / WT_MAX_STREAMS (6.7) の 3 カプセル (6.10 の WT_STREAMS_BLOCKED には減少値の受信側 MUST は存在しない。WT_STREAMS_BLOCKED は「ブロック発生時点の上限」の通知 (advisory) であり、受信側の検証は仕様で要求されないため検証対象外とする)
  - 2^60 上限検知: WT_MAX_STREAMS (6.7) / WT_STREAMS_BLOCKED (6.10) の 2 カプセル (WT_STREAMS_BLOCKED は現在ペイロード未解析のため、varint 解析を含む新規ハンドラ (`handle_wt_streams_blocked` 等) を追加する)
- **減少値検知**: 各ハンドラで格納済みの受信値 (前回受信値。`max_data_local` / `max_stream_data_local` / `max_streams_bidi_local` / `max_streams_uni_local`) と比較し、減少していれば WT_FLOW_CONTROL_ERROR でセッションを閉じる
- **2^60 上限検知**: Maximum Streams (WT_MAX_STREAMS / WT_STREAMS_BLOCKED 両方) が 2^60 を超える値を受信したら WT_FLOW_CONTROL_ERROR でセッションを閉じる
- **セッション閉鎖の実現方式**: 既存の「0x50 の Error イベントを push するだけ」のパターンはセッションを閉じないため使わない。`send_stream_data` の送信超過 (webtransport_h2.cpp) と同じく `close_session(session_id, 0x50, ...)` を直接呼んで閉じる。0x50 は draft-15 Section 3.4 の 0xTBD のプレースホルダ (issue 0086 でコメント注記を対応予定。0097 が新設するハンドラの箇所にも同様の注記を付ける。0086 が先に実装された場合はその方針に合わせる)
- **比較基準の注意**: 格納済み受信値は `apply_peer_initial_flow_control` で対向 SETTINGS から初期化され、対向 SETTINGS が 0 の場合は自側 config 値へフォールバックする (issue 0103 で修正予定。0103 のスコープは WT_MAX_DATA / WT_MAX_STREAM_DATA のデータクレジットであり、`max_streams_bidi_local` / `max_streams_uni_local` のフォールバック除去を含む保証はない)。フォールバック値は「受信値」ではないため、比較基準は「受信値 (非 0 SETTINGS 値・カプセル受信値) のみ」とし、フォールバック値を減少値判定の基準にしない (フォールバック値より小さい正当な最初の更新を「減少」扱いする false positive を防ぐ)。0103 の実装順序に合わせて調整する
- **スコープ外の明記**: Section 6.6 の別 MUST「WT_STOP_SENDING 後に WT_MAX_STREAM_DATA を受信したら WT_STREAM_STATE_ERROR」(draft 1090-1095) は本 issue のスコープ外とする (担当 issue 未定。0098 は「同一ストリームへの 2 回目の WT_STOP_SENDING 受信検出」のみを担当し、自ら WT_STOP_SENDING を送信した状態の追跡は含まないため)
- **実装競合の注意**: 受信フロー制御違反でセッションを閉じる経路 (0x50 で close_session) は issue 0099 と近いため、実装順序と変更の衝突を考慮する
- 減少値・2^60 上限のテストを追加する (Sans-IO 構成でワイヤ注入)
- 変更内容を CHANGES.md の `## develop` に [FIX] として記載する

## 完了条件

- 減少値の WT_MAX_DATA / WT_MAX_STREAM_DATA / WT_MAX_STREAMS 受信で WT_FLOW_CONTROL_ERROR (0x50) によるセッション閉鎖が発生する
- 2^60 超の Maximum Streams (WT_MAX_STREAMS / WT_STREAMS_BLOCKED) 受信で WT_FLOW_CONTROL_ERROR によるセッション閉鎖が発生する
- WT_STREAMS_BLOCKED の減少値はエラーにしない (仕様に受信側 MUST がなく、advisory な通知のため検証対象外)
- テストが追加され通る
