# HTTP/2 で不正なストリーム状態への WT_STREAM / WT_RESET_STREAM 受信を検知しない問題を修正する

- Created: 2026-08-15
- Completed: 2026-08-15
- Branch: feature/fix-h2-stream-state-error
- Polished: 2026-08-15

## 目的

draft-ietf-webtrans-http2-15 の MUST 違反を修正する:

- Section 6.4: 不正な状態のストリームへの WT_STREAM capsule 受信で stream error (Section 3.4) の WT_STREAM_STATE_ERROR を送る MUST
- Section 6.2: WT_RESET_STREAM の Reliable Size が受信済みバイト数と一致しない場合に session error の WT_STREAM_STATE_ERROR でセッションを閉じる MUST、および不正な状態のストリームへの WT_RESET_STREAM capsule 受信で stream error を送る MUST

これらが未実装のため、非コンプライアントなピアからの不正カプセルを検知できない。

## 現状

- `handle_wt_stream` はストリームが存在しない場合に暗黙的に作成する (Section 6.4 の暗黙作成。最初の受信では正しい)。ピアの WT_RESET_STREAM 受信後・WT_STREAM_FIN 受信後はエントリが残存したまま状態検証なしで以後の WT_STREAM / WT_RESET_STREAM を処理する (MUST 違反)
- 自側の `reset_stream` は `wt_session->streams` からエントリを erase するため、その後のピアからの WT_STREAM は「新規作成」として再生成される。QUIC 意味論 (Section 5.2) では自側の送信リセットは受信側に影響しないため、この再生成は受信追跡 (`bytes_received` / `recv_state`) の喪失を伴う不正確さがある
- ストリーム状態の管理機構 (`WtStreamInfo` の `send_state` / `recv_state` / `StreamState` 列挙型) は `src/bindings/webtransport_h2.h` に定義されているが、受信ハンドラで使用されておらず、状態遷移も更新されない
- `handle_wt_reset_stream` は Reliable Size を「無視」しており (コメントに明記)、受信済みバイト数 (`bytes_received`) との一致を検証しない
- WT_STREAM_STATE_ERROR のコード値は Section 3.4 で 0xTBD (未確定)。既存のフロー制御違反は 0x50 をプレースホルダとして使用している
- 同種の状態検証 MUST (Section 6.3 の WT_STOP_SENDING 二重受信 / Section 6.6 の WT_MAX_STREAM_DATA / Section 6.9 の WT_STREAM_DATA_BLOCKED 等) も未実装だが、本 issue のスコープ外とする

## 設計方針

- 受信側の状態機械を実装する: `handle_wt_stream` で WT_STREAM_FIN 受信時に `recv_state` を DataRecvd に、`handle_wt_reset_stream` で Reliable Size 一致時に `recv_state` を ResetRecvd に更新する (Section 5.2 の QUIC 状態ミラー。`StreamState` の DataRead / ResetRead はアプリのイベント消費追跡を要するため、本実装の recv_state は受信側の状態のみを表す)
- `handle_wt_stream`: ストリームが既に存在する場合は `recv_state` を確認し、受信側終端状態 (DataRecvd / ResetRecvd) への WT_STREAM 受信で stream error (WT_STREAM_STATE_ERROR) を検知する (状態検知は既存のフロー制御チェックより前に置く。フロー制御違反の error code 0x50 と区別するため)。存在しない場合は従来どおり暗黙作成する
- `handle_wt_reset_stream`: ストリームが存在する場合は Reliable Size と受信済みバイト数 (`bytes_received`) の一致を検証し、不一致時に session error を検知する。ストリームが存在しない場合 (真に未知のストリーム) は受信済みバイト数 0 として比較し、Reliable Size = 0 なら受け入れ (暗黙作成と同様の初期化でエントリを作成し、受信側の開始として ResetRecvd へ遷移)、> 0 なら session error とする (Section 6.2 の MUST)。あわせて、受信側終端状態 (DataRecvd / ResetRecvd) のストリームへの WT_RESET_STREAM 受信も stream error として検知する。エラー検知時は StreamReset イベントを push せず、セッションエラーの送出に進む
- 自側 `reset_stream` のエントリ管理を見直す: 送信リセットは送信側の終了のみであり受信側は継続するため (Section 5.2 の QUIC 意味論)、エントリを erase せず `send_state` を ResetSent に更新して受信側の追跡 (`bytes_received` / `recv_state`) を維持する。リセット済みストリームへの `send_stream_data` は `send_state` の確認で塞ぐ (Section 6.4 の「WT_STREAM capsule MUST NOT be sent after a stream is closed or reset」。塞ぐのは ResetSent のみとし、FIN 送信後の DataSent 遷移・FIN 後の再送信の塞ぎ・reset_stream の再呼び出し (ResetSent 後の再 reset) の扱いは本 issue のスコープ外とする)。両ハーフ終端後のエントリは明示的な削除を行わず、既存のストリームと同様にセッション終了まで保持する (`get_stream_ids` にはリセット済みストリームも含まれるようになる)。既存テストへの影響を確認しながら実装する
- セッションエラーの送出は受信ハンドラ内で `close_session` を直接呼ぶ (mem_recv コールバック中でも `close_session` は送信をキューするのみで nghttp2_session_send を呼ばないため安全。mem_recv 内の resume_data は `handle_wt_close_session` の END_STREAM 応答で実績あり)。エラー検知後の同一 receive() 内の後続カプセルは、`process_capsules` のループ冒頭で `is_terminated` を確認して遮断し、終了済みセッションに処理されないようにする (close_session の再呼び出しによる WT_CLOSE_SESSION の二重キューも防ぐ。この遮断は 0082 の close_session ガードとは独立に必要)。stream error と session error は実装上区別せず、いずれも `close_session` (WT_CLOSE_SESSION 送出 + END_STREAM) で実現する (Section 3.4 の「Prior to terminating a stream with an error, a WT_CLOSE_SESSION capsule with an application-specified error code MAY be sent」)
- WT_STREAM_STATE_ERROR のコード値は draft で 0xTBD のため、既存の FLOW_CONTROL_ERROR (0x50) と同様のプレースホルダ方式で 0x51 を仮採用し、draft で値が確定したら更新する旨をコメントで明記する
- 既存の受信側フロー制御違反 (Error イベント 0x50 のみで送出しない) は高レベル層 (client.py / server.py) で ERROR イベントが処理されない既知の問題のためスコープ外とし、変更しない
- テスト: 0070 の Sans-IO 構成 (ワイヤ注入) を踏襲し、次を検証する: ① リセット済み / FIN 済みストリームへの WT_STREAM 受信と受信側終端状態への WT_RESET_STREAM 受信で WT_CLOSE_SESSION (error code 0x51) が送出されること ② Reliable Size 不一致の WT_RESET_STREAM 受信で error code 0x51 が送出されること ③ ストリーム不在の WT_RESET_STREAM は Reliable Size = 0 なら受け入れ (エラーが送出されないことと、その後の WT_STREAM が ResetRecvd としてエラーになることで観測する)・> 0 なら error になること ④ 正常系 (暗黙作成・Reliable Size 一致・自側 reset 後の受信) が従来どおり動作すること
- 変更対象: `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (状態遷移・コメント・docstring 更新) / テスト / `CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- ピアの WT_RESET_STREAM 受信済み (ResetRecvd) のストリームへの WT_STREAM 受信で WT_STREAM_STATE_ERROR が送出される
- ピアの FIN 受信済み (DataRecvd) のストリームへの WT_STREAM 受信で WT_STREAM_STATE_ERROR が送出される
- 受信側終端状態 (DataRecvd / ResetRecvd) のストリームへの WT_RESET_STREAM 受信で WT_STREAM_STATE_ERROR が送出される
- Reliable Size 不一致の WT_RESET_STREAM 受信で WT_STREAM_STATE_ERROR が送出される (ストリーム不在時は受信済みバイト数 0 として比較)
- 正常系 (暗黙作成・Reliable Size 一致・自側 reset 後のピアからの受信・ストリーム不在の WT_RESET_STREAM の Reliable Size = 0 での受け入れ) は従来どおり動作する
- 全テストが通る

## 解決方法

- `handle_wt_stream` に受信側終端状態 (DataRecvd / ResetRecvd) のストリームへの WT_STREAM 受信の検知を追加し (フロー制御チェックより前に置き 0x50 と区別)、FIN 受信で `recv_state` を DataRecvd に遷移させた
- `handle_wt_reset_stream` を実装した: ストリーム不在時は Reliable Size を受信済みバイト数 0 と比較 (0 ならエントリ作成 + ResetRecvd 遷移、> 0 なら session error)、終端状態への受信は stream error、Reliable Size と `bytes_received` の一致検証 (不一致は session error)、一致時に ResetRecvd へ遷移。エラー検知時は StreamReset イベントを push しない
- エラー検知は `report_stream_state_error` に集約した: Error イベント (0x51) を push してから close_session (WT_CLOSE_SESSION 送出 + END_STREAM) を呼ぶ。`process_capsules` のループ冒頭で is_terminated を確認して同一 receive() 内の後続カプセルを遮断し、バッファ残留分も破棄する
- 自側 `reset_stream` はエントリを erase せず `send_state` を ResetSent に更新する (受信側の追跡維持)。`send_stream_data` は ResetSent 状態への送信を塞ぐ
- 0x51 は WT_STREAM_STATE_ERROR (0xTBD) のプレースホルダとしてコメントで明記した
- `tests/test_webtransport_h2_stream_state_error.py` を新規作成し、テスト 16 本を追加した (終端状態への両カプセル受信・Reliable Size 不一致・未知ストリームの受入/拒否・後続カプセル遮断・ピア側 SessionClosed・フロー制御超過優先・正常系 5 本)。ガード無効化ビルドで 8 本失敗すること、is_terminated 遮断無効化で遮断テストが失敗することを確認し、修正の検証として機能することを実証した
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
- 全テスト (663 本) が通ることを確認した
