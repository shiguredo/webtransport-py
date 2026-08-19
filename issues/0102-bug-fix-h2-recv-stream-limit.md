# WebTransport over HTTP/2 の受信ストリーム数上限検知を実装する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-recv-stream-limit
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http2-15 Section 6.7 の MUST「広告した Maximum Streams を超えるストリームの受信は WT_FLOW_CONTROL_ERROR でセッションを閉じる」を実装する。あわせて同節の Note「ストリーム ID の暗黙オープン規則 (同じタイプ・方向のより低い ID のストリームも暗黙に開かれる)」と「閉じたストリームも含めて累積カウントする」を考慮する。現状は受信側のストリーム数制限が未検証で、ピアが無制限にストリームを開け、エントリ無制限作成によるメモリ増加も発生し得る。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_stream` の暗黙作成 (未知ストリームの WT_STREAM 受信時) は `max_streams_bidi_remote` / `max_streams_uni_remote` を一切検証しない
- `WtSessionInfo::max_streams_bidi_remote` / `max_streams_uni_remote` は設定のみで読み出し箇所が無い (検証対象の値は `config_.wt_initial_max_streams_*` 由来であり、SETTINGS / WT_MAX_STREAMS カプセルで広告する値と一致する)
- ストリーム ID の暗黙オープン規則 (同じタイプ・方向のより低い ID のストリームも暗黙に開かれる) も未考慮
- 同関数 `handle_wt_stream` を変更する open issue 0099 (受信データ量超過時の close_session 直接呼び出し) があり、実装順序と方式の統一が必要

## 設計方針

- **対象スコープ**: ストリームを暗黙作成する 2 つの経路の両方を対象とする:
  - `handle_wt_stream` の暗黙作成 (未知ストリームへの WT_STREAM 受信。空カプセル (length == 0) は早期 return されるため、検証は `length == 0` チェックの後かつエントリ作成前の共通位置に置く)
  - `handle_wt_reset_stream` の暗黙作成 (真に未知のストリームへの WT_RESET_STREAM (Reliable Size = 0) でエントリを作成する経路。この経路も制限検知の対象に含める。検証は Reliable Size = 0 の確認後かつエントリ作成前に置く。Section 6.7 の MUST はカプセル種別を限定していない)
- **カウント方式**: Section 6.7 の Note どおり「同じタイプ (双方向 / 単方向) と方向 (クライアント起点 / サーバー起点) のより低い ID のストリームも暗黙に開かれたとみなす」累積カウントとする (閉じたストリームも含めてカウントし、クライアント起点 %4==0 / サーバー起点 %4==1 の双方向と、%4==2 / %4==3 の単方向を区別する)。受信したストリーム ID から暗黙に開かれるストリーム数 (同一タイプ・方向のストリーム数 = (stream_id >> 2) + 1) を算出し、広告した Maximum Streams と比較して超過を検知する
- **セッション閉鎖の実現方式**: issue 0099 と同一の方式に統一する (Error イベント push + `close_session(session_id, 0x50, ...)` 直接呼び出し。0x50 は draft-15 Section 3.4 の 0xTBD プレースホルダ)。受信ストリーム数超過の 0x50 も 0099 の `on_error` 通知対象に含まれる (0099 は error_code 0x50 のみを on_error に渡すフィルタ方式のため自動的に含まれる)。制限超過検知時は Error イベントの push のみ行い、StreamReset イベントは push しない (closed issue 0084 のエラー検知パターンと同じ)
- **検証順序**: `handle_wt_reset_stream` で複数エラーが同時成立し得る入力の検証順序は、0101 の error_code 範囲検証 (0x52) → 既存の Reliable Size 検証 (0x51) → 本 issue のストリーム数制限 (0x50) の順とする (0101 はハンドラ冒頭・0084 の Reliable Size 検証は制限チェックより前)
- **実装競合の注意**: 同一関数を変更する open issue と実装順序・方式を調整する。`handle_wt_stream` は 0099 (受信データ量超過の close_session 化) が、`handle_wt_reset_stream` は 0101 (error_code 範囲検証) が変更対象。0097 (フロー制御カプセルの受信値検証) は 0x50 で close_session する閉鎖方式を共有する。0086 (0x50 プレースホルダ注記) は 0102 が新設する 0x50 使用箇所にも注記方針を適用する (0086 が先に実装された場合はその方針に合わせる)。あわせて 0124 (死にコード削除) が `WtSessionInfo::max_streams_bidi_remote` / `max_streams_uni_remote` を削除対象としているため、本 issue の実装で同フィールドを読み出す旨を 0124 側へ伝え削除対象から外す (広告値の増加送信 (WT_MAX_STREAMS) への将来対応を考えると、現在の広告値を保持する同フィールドを読み出すのが意味論的に正しい。config 直読みは増加送信に対応できない)。0099 の「受信超過」の文言へのストリーム数超過の包含は 0099 実装時の作業として扱い、本 issue からは 0x50 が `on_error` に自動的に含まれることのみを規定する
- **テスト**: 広告した Maximum Streams ちょうどまで OK・上限 +1 で WT_FLOW_CONTROL_ERROR になる境界、暗黙オープン規則 (高い ID の受信で低い ID もカウントされる)、閉じたストリームの累積カウント、WT_RESET_STREAM 経由の超過を追加する (Sans-IO 構成でワイヤ注入。`tests/test_webtransport_h2_stream_state_error.py` の既存パターンに従う。広告値は config の `wt_initial_max_streams_*` を小さく設定して作る)
- 変更内容を CHANGES.md の `## develop` に [FIX] として記載する

## 完了条件

- 広告した Maximum Streams を超えるストリーム受信 (WT_STREAM / WT_RESET_STREAM の両経路) で WT_FLOW_CONTROL_ERROR (0x50) によるセッション閉鎖が発生する
- 暗黙オープン規則 (同じタイプ・方向の低い ID もカウント・閉じたストリームの累積) を考慮した検知が機能する
- テストが追加され、全テストが通る
