# QPACK デコードブロック中の受理前 FIN が検知されない

- Created: 2026-08-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-qpack-blocked-pre-accept-fin-loss
- Polished: {YYYY-MM-DD}

## 目的

QPACK デコードブロック中に届いた受理前 FIN (サーバーが応答を送信する前に CONNECT ストリームが FIN で閉じられた) の fin が完全に喪失し、以後どの経路でもセッション終了が検知されない問題を修正する。0058 で対応した fin 引数による検知は、ヘッダーが fin 到着時点でデコード済みの場合に限定される。

## 現状

- 0058 の検知条件は `session_ids_.count(stream_id) > 0` (ヘッダー処理完了 = `end_headers_cb` 実行済み) に依存する。ヘッダーが QPACK デコードブロック中に fin 付きデータが届くと、ヘッダー未処理のため検知が成立しない
- nghttp3 の挙動 (実測確認済み):
  - ブロック中のデータは `nghttp3_stream_buffer_data` で inq にバッファされる (ヘッダーは後で正しくデコードされ、`session_ids_` に挿入されて SESSION_READY は発火する)
  - しかし空 FIN (srclen == 0) はバッファされず、`NGHTTP3_STREAM_FLAG_READ_EOF` として保存されるだけのため、ブロック解除後の `process_blocked_stream_data` では fin が再処理されず `end_stream` コールバックは発火しない
  - 結果として fin は完全に喪失し、以後どの経路 (fin 引数検知・end_stream コールバック) でも検知されない。セッションは確立されるが終了検知されず、`session_ids_` に残り続ける (接続終了まで)
- 発生条件は QPACK エンコーダーストリームの到着遅延 (パケットロス等) で発生し得る

## 設計方針

- 検知経路の候補:
  - `receive_stream_data` に渡る fin 引数で「fin が渡ったが session_ids_ に未挿入 (ヘッダー未処理) のストリーム」を保留集合に一時記録し、後で `end_headers_cb` が CONNECT 判定 (`session_ids_` への挿入) を行った時点で受理前 FIN とみなす
  - 実現可能性の調査が必要 (nghttp3 の状態機械との整合、`end_headers_cb` と保留集合のタイミング)
- 検知後は 0058 と同じ遅延クローズ (accept_session 受理 + 2xx 書き出し完了後に `close_stream`) を流用する
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h`、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- QPACK デコードブロック中の受理前 FIN でも、セッション終了が検知されて `session_ids_` から削除され、SessionClosed イベント (error_code 0) が発火する
- 通常の受理前 FIN (0058 の対応済み経路) は影響を受けない
- モックなしの Sans-IO テストで検証できる (QPACK エンコーダーストリームの到着を遅延させる構成)
