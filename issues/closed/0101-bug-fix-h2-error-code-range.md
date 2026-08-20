# WebTransport over HTTP/2 の WT_RESET_STREAM / WT_STOP_SENDING 受信時の error_code 範囲検証を実装する

- Created: 2026-08-18
- Completed: 2026-08-20
- Branch: feature/fix-h2-error-code-range
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http2-15 Section 6.2 / 6.3 の MUST「Application Protocol Error Code は 0xffffffff を超えてはならず、超える値は WT_ERROR セッションエラーとして扱う」を実装する。現状は varint デコードした error_code を静かに `uint32_t` へ切り詰めてアプリに渡すため、ワイヤの不正値を検知できない。制約の定義元は [WEBTRANSPORT-H3] Section 4.4 (H3 側の同種対応は issue 0095 が担当)。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_reset_stream` と `H2Session::handle_wt_stop_sending` は error_code を varint デコードした後、`static_cast<uint32_t>` で切り詰めてイベントにする
- 2^62-1 までの値が送られると下位 32bit が誤ってアプリへ渡る
- 同一関数 (`handle_wt_stop_sending`) を変更対象とする open issue 0098 (WT_STOP_SENDING 二重受信検出) があり、実装順序と変更の衝突を考慮する必要がある

## 設計方針

- **変更対象**: `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_reset_stream` / `handle_wt_stop_sending` / テスト / CHANGES.md
- **検証**: 受信した error_code が 0xffffffff を超える場合 (0xffffffff ちょうどは合法) は WT_ERROR セッションエラーとしてセッションを閉じる
- **WT_ERROR のプレースホルダ値**: issue 0100 と同一の値 (例: 0x52。0x50 / 0x51 と衝突しない値) を使う。draft-15 Section 3.4 の 0xTBD のプレースホルダであり、draft で値が確定したら更新する (issue 0086 の注記方針に合わせる)
- **セッションエラーの実現方式**: issue 0100 と同じ方式とする。Error イベント (WT_ERROR) を push し、固定の短い英語メッセージを `close_session(session_id, wt_error_code, ...)` に渡してセッションを閉じる (`report_stream_state_error` は 0x51 固定のため直接呼ぶ)。close_session はエントリ削除前に呼ぶ。検知側はエントリを残したまま is_terminated を立てるため、その後のピアの END_STREAM 受信時に `on_stream_close_callback` が SessionClosed を後発 push する (closed issue 0084 と同じ挙動。許容する)
- **検証位置と順序**: error_code 範囲検証はハンドラの冒頭 (varint デコード直後) で行い、他の検証 (終端状態・reliable_size 検証等) より先に実行する。0098 との同時成立時 (2 回目受信かつ error_code が 0xffffffff 超) は、本 issue の error_code 範囲検証を先に行い、通過後に 0098 の二重受信検証を行う (error_code 範囲検証が 0101 の担当であることは 0098 に規定済み)
- **テスト**: 0xffffffff 超の error_code を含む WT_RESET_STREAM / WT_STOP_SENDING 受信で WT_ERROR セッションエラーが発生すること (ワイヤ上に WT_CLOSE_SESSION (プレースホルダ値) が送出されることと、Error イベント (プレースホルダ値) の両方を検証する)、0xffffffff ちょうどの error_code はエラーにならず従来どおり StreamReset / StopSending イベントが届くことを追加する。既存の `_assert_state_error_sent` は 0x51 固定のため、WT_ERROR (0x52) の検証には 0x51 を差し替えた類似ヘルパー (またはパラメータ化) が必要。WT_STOP_SENDING (Type 0x190B4D3A) 用のワイヤ注入ヘルパーは存在しないため新規作成する (WT_RESET_STREAM 用の流用で自明)
- 変更内容を CHANGES.md の `## develop` に [FIX] として記載する

## 完了条件

- 0xffffffff 超の error_code を含む WT_RESET_STREAM / WT_STOP_SENDING 受信で WT_ERROR セッションエラーが発生する (ワイヤ上に WT_CLOSE_SESSION (プレースホルダ値) が送出される)
- 0xffffffff ちょうどの error_code はエラーにならない
- テストが追加され、全テストが通る

## 解決方法

- `H2Session::handle_wt_reset_stream` / `handle_wt_stop_sending` で error_code を varint デコードした直後に範囲を検証する。0xffffffff 超なら既存の `report_wt_error` 経由で Error イベント (0x52) を push し、`close_session` する
- 範囲検証は終端状態・Reliable Size 不一致・未知ストリームの non-zero Reliable Size・二重受信 (0x51) より先に行う。0xffffffff ちょうどは従来どおり StreamReset / StopSending に渡す
- `tests/test_webtransport_h2_error_code_range.py` で超過・上限ちょうど・他 MUST との同時成立を検証した
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
- 全テスト (710 本) が通ることを確認した
