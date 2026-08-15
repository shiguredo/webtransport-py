# HTTP/2 で不正なストリーム状態への WT_STREAM / WT_RESET_STREAM 受信を検知しない問題を修正する

- Created: 2026-08-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-stream-state-error
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 の MUST 違反を修正する。Section 6.4 の「不正な状態のストリームへの WT_STREAM capsule 受信で WT_STREAM_STATE_ERROR セッションエラーを送る MUST」と、Section 6.2 の「WT_RESET_STREAM の Reliable Size が受信済みバイト数と一致しない場合に WT_STREAM_STATE_ERROR セッションエラーでセッションを閉じる MUST」が未実装のため、非コンプライアントなピアからの不正カプセルを検知できない。

## 現状

- `src/bindings/webtransport_h2.cpp` の `handle_wt_stream` はストリームが存在しない場合に暗黙的に作成する (Section 6.4 の暗黙作成。最初の受信では正しい) が、リセット済み・クローズ済みのストリーム ID への WT_STREAM capsule 受信も「新規作成」として処理してしまう (Section 6.4 の MUST 違反)
- ストリーム状態の管理機構 (`WtStreamInfo` の `send_state` / `recv_state` / `StreamState` 列挙型) は `src/bindings/webtransport_h2.h` に定義されているが、受信ハンドラで使用されておらず、状態遷移も更新されない
- `handle_wt_reset_stream` は Reliable Size を「無視」しており (コメントに明記)、受信済みバイト数 (`WtStreamInfo::bytes_received`) との一致を検証しない (Section 6.2 の MUST 違反)
- 結果として、閉じた / リセットされたストリームへの WT_STREAM、Reliable Size 不一致の WT_RESET_STREAM を検知できず、WT_STREAM_STATE_ERROR セッションエラー (Section 3.4) を送出しない

## 設計方針

- `handle_wt_stream`: ストリームが既に存在する場合は `recv_state` を確認し、終了状態 (ResetRecvd / DataRead 等) のストリームへの WT_STREAM 受信でセッションエラーを検知する。存在しない場合は従来どおり暗黙作成する
- `handle_wt_reset_stream`: Reliable Size と受信済みバイト数 (`bytes_received`) の一致を検証し、不一致時にセッションエラーを検知する
- エラーの検知手段とセッションエラーの送出方法 (Error イベント経由か `close_session` 経由か) は、既存のフロー制御違反 (Section 6.5 / 6.6) の処理との整合を取って設計する
- 変更対象: `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (コメント・docstring 更新) / テスト / `CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- リセット済みストリームへの WT_STREAM 受信で WT_STREAM_STATE_ERROR セッションエラーが送出される
- Reliable Size 不一致の WT_RESET_STREAM 受信で WT_STREAM_STATE_ERROR セッションエラーが送出される
- 正常系 (暗黙作成・Reliable Size 一致) は従来どおり動作する
- 全テストが通る
