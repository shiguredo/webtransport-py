# WebTransport over HTTP/2 の WT_STOP_SENDING 二重受信検出を実装する

- Created: 2026-08-18
- Completed: 2026-08-20
- Branch: feature/fix-h2-stop-sending-duplicate
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http2-15 Section 6.3 の MUST「同一ストリームへの 2 回目の WT_STOP_SENDING 受信は WT_STREAM_STATE_ERROR を送る」を実装する。現状は 2 回目を受信しても何も検知せず、仕様違反のピアを検知できない。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_stop_sending` は状態追跡が一切なく、毎回 StopSending イベントを push するだけ
- 受信済みかどうかの記録が無く、2 回目の受信を識別できない
- 同一関数を変更対象とする open issue 0101 (error_code の 0xffffffff 超検証) があり、実装順序と変更の衝突を考慮する必要がある

## 設計方針

- **変更対象**: `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_stop_sending` / `src/bindings/webtransport_h2.h` (追跡構造の追加) / テスト / CHANGES.md
- **追跡構造**: セッション単位の受信済みストリーム ID 集合 (`std::set<uint64_t>` 等のセッション内集合) で管理する。`WtStreamInfo` へのフラグ追加では、エントリが存在しないストリーム (仕様違反ピアが未知ストリーム ID 宛に送るケース) への 2 回目の受信を検出できないため採用しない。また、エントリの暗黙作成は `get_stream_ids` に偽のストリームを露出させる副作用があるため行わない
- **処理フロー**:
  - 1 回目の受信: 従来どおり `H2EventType::StopSending` イベントを push し、受信済み集合にストリーム ID を記録する
  - 2 回目の受信: StopSending イベントを push せず、既存の `report_stream_state_error` で WT_STREAM_STATE_ERROR (0x51) を送出してセッションを閉じる (0x51 は draft-15 Section 3.4 では 0xTBD のプレースホルダ。closed issue 0084 で仮採用された値。送出方式は 0084 の WT_RESET_STREAM の状態エラー検知と同じく、`report_stream_state_error` による Error イベント push + `close_session` で実現する)
  - 受信済み集合のライフサイクルはセッション終了まで (セッション単位で削除される。ストリーム ID はセッション内で再利用されない)
- **スコープ外の明記**: Section 6.6 の隣接 MUST「WT_STOP_SENDING 後に WT_MAX_STREAM_DATA を受信したら WT_STREAM_STATE_ERROR」(draft 1090-1095) は本 issue のスコープ外 (担当 issue 未定)
- **実装競合の注意**: 同一関数を変更する issue 0101 (error_code 範囲検証) との実装順序を考慮する。両条件が同時成立する入力 (2 回目受信かつ error_code が 0xffffffff 超) では、0101 の error_code 範囲検証を先に行い、通過後に二重受信検証を行う (error_code 範囲検証は 0101 の担当)
- テストを追加する: 2 回目の WT_STOP_SENDING 受信で WT_STREAM_STATE_ERROR (0x51) によるセッション閉鎖、1 回目は従来どおり StopSending イベントが届くこと。`tests/test_webtransport_h2_stream_state_error.py` の既存パターン (ワイヤ注入 + `_assert_state_error_sent`) に従う
- 変更内容を CHANGES.md の `## develop` に [FIX] として記載する

## 完了条件

- 同一ストリームへの 2 回目の WT_STOP_SENDING 受信で WT_STREAM_STATE_ERROR (0x51) が送出されセッションが閉じる
- 1 回目の WT_STOP_SENDING 受信では従来どおり StopSending イベントが届く
- テストが追加され、全テストが通る

## 解決方法

- `WtSessionInfo` に受信済み WT_STOP_SENDING の Stream ID 集合 `received_stop_sending_stream_ids` を追加した。未知ストリームでも 2 回目を検出するため `WtStreamInfo` のフラグにはせず、暗黙のストリーム作成もしない
- `H2Session::handle_wt_stop_sending` で 1 回目は従来どおり StopSending イベントを push して集合に記録し、2 回目はイベントを push せず `report_stream_state_error` (error code 0x51) で Error イベント push と `close_session` を行う
- 集合の寿命はセッション破棄までとし、ストリーム単位では消さない
- `tests/test_webtransport_h2_stream_state_error.py` に 1 回目イベント・2 回目のセッション閉鎖・未知ストリーム・別ストリーム・同一 receive() 内の連結カプセルのテストを追加した
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
- 全テスト (686 本) が通ることを確認した
