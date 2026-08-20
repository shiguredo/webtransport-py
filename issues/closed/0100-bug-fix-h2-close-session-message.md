# WebTransport over HTTP/2 の WT_CLOSE_SESSION 受信時のメッセージ検証 (1024 バイト・UTF-8) を実装する

- Created: 2026-08-18
- Completed: 2026-08-20
- Branch: feature/fix-h2-close-session-message
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http2-15 Section 6.12 の MUST「Application Error Message が 1024 バイト超または不正な UTF-8 の場合、受信者は WT_ERROR セッションエラーとして扱う」を実装する。現状は受信メッセージを無検証でアプリに渡す。H3 側の同種対応は issue 0096 が担当する。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_close_session` はペイロード長・UTF-8 を検証せず、エラーコードとメッセージをそのままアプリへ渡す (`length >= 4` のみ確認)
- 送信側の UTF-8 境界トリミングは別 issue (0085) で対応予定だが、受信側の検証は本 issue の対象 (0085 の「現状」にも受信側はスコープ外と明記されている)
- WT_ERROR のコード値は draft-15 Section 3.4 で 0xTBD (未確定)。既存の WT_STREAM_STATE_ERROR (0x51) と同様のプレースホルダ方式で仮採用し、draft で値が確定したら更新する

## 設計方針

- **変更対象**: `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_close_session` / テスト / CHANGES.md
- **検証**: メッセージ長 (1024 バイト超。ちょうど 1024 バイトは合法) と UTF-8 妥当性を検証し、違反時は WT_ERROR セッションエラーとする
- **セッションエラーの実現方式**: 既存の `report_stream_state_error` (Error イベント push + `close_session`) と同じ方式で、WT_ERROR のプレースホルダ値 (0x51 と衝突しない値。例: 0x52) を `close_session(session_id, wt_error_code, ...)` に渡してセッションを閉じる。`report_stream_state_error` は 0x51 固定のため直接 `close_session` を呼ぶ
- **エラー時の error_message**: 受信した不正メッセージをそのまま `close_session` へ渡さない。固定の短い英語メッセージを定数化して渡し、テストのワイヤ検証と一致させる (0084 の `_WT_STREAM_TERMINAL` パターンと同じ)。理由: (a) 不正 UTF-8 がそのままワイヤへ再送出される、(b) `close_session` の切り詰め (`std::min(size, 1024)`) は 0085 対応前は UTF-8 文字境界を無視するため、切断で新たな不正 UTF-8 が生成され得る。Error イベント (WT_ERROR) の error_message も同じ固定メッセージにする (0084 の `report_stream_state_error` パターンと同じく、Error イベントと close_session の両方に同じメッセージを渡す)
- **後始末との順序**: エラー検知時は既存の正常時後続処理 (SessionClosed push → エントリ削除 → END_STREAM 応答) を実行しない。`close_session` はエントリ削除前に呼ぶ (削除後はガードで塞がれる。またエントリ削除を先に実行すると close_session がキューした WT_CLOSE_SESSION が `http2_stream_buffers_` の削除で未送出になる)。エラー検知時点では Error イベント (WT_ERROR) のみ push する。なお、検知側はエントリを残したまま `is_terminated` を立てるため、その後のピアの END_STREAM 受信時に `on_stream_close_callback` が SessionClosed (error code 0) を後発 push する (closed issue 0084 の 0x51 経路と同じ挙動。この後発 SessionClosed は許容する)
- **完了条件の観測点**: ワイヤ上の WT_CLOSE_SESSION (プレースホルダ値) の送出と、Error イベント (プレースホルダ値) の両方を検証する (既存の `tests/test_webtransport_h2_stream_state_error.py` の `_assert_state_error_sent` パターンに従う)
- **テスト**: 1024 バイト超・不正 UTF-8 の 2 系統を分離して追加する。既存のワイヤ注入ヘルパー `_encode_wt_close_session_capsule` は Length 1 バイト varint (64 バイト未満) 前提のため、1024 バイト超ペイロードの注入には Length 拡張が必要。不正 UTF-8 は Python の str では生成できないため、bytes を受け付ける形への拡張 (または別ヘルパー) も必要。完了条件 3 (1024 ちょうど・合法 UTF-8) は、エラー送出がないことと SessionClosed が正常に push されることの両方を確認する
- 変更内容を CHANGES.md の `## develop` に [FIX] として記載する

## 完了条件

- 1024 バイト超のメッセージを含む WT_CLOSE_SESSION 受信で WT_ERROR セッションエラーが発生する (ワイヤ上に WT_CLOSE_SESSION (プレースホルダ値) が送出される)
- 不正な UTF-8 のメッセージを含む WT_CLOSE_SESSION 受信で WT_ERROR セッションエラーが発生する (ワイヤ上に WT_CLOSE_SESSION (プレースホルダ値) が送出される)
- 1024 バイトちょうど・正しい UTF-8 のメッセージはセッションエラーにならない
- テストが追加され、全テストが通る

## 解決方法

- `H2Session::handle_wt_close_session` で Application Error Message のバイト長と UTF-8 妥当性を検証する。1024 バイト超または RFC 3629 として不正な UTF-8 なら `report_wt_error` 経由で Error イベント (0x52) を push し、`close_session` する
- 0x52 は `kWtError` にまとめ、draft-15 Section 3.4 の WT_ERROR (0xTBD) プレースホルダである旨を注記した。draft で値が確定したら更新する
- 受信した不正メッセージは `close_session` に渡さず、固定の英語メッセージを Error と WT_CLOSE_SESSION の両方に使う。エラー時は SessionClosed push / エントリ削除 / 受信者側 END_STREAM 応答を行わない
- `tests/test_webtransport_h2_close_session_message.py` で 1024 バイト超・不正 UTF-8 (孤立 continuation / overlong)・1024 バイトちょうど・短い合法 UTF-8 を検証した
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
- 全テスト (703 本) が通ることを確認した
