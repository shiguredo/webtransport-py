# WebTransport over HTTP/2 の受信フロー制御違反でセッションが閉じない問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-recv-flow-control-session-close
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http2-15 Section 6.5 / 6.6 の MUST「受信データが広告した WT_MAX_DATA / WT_MAX_STREAM_DATA を超えたら WT_FLOW_CONTROL_ERROR でセッションを閉じる」を実装する。現状は Error イベントを push するだけでセッションが閉じず、ピアが制限超過データを送り続けられる。本 issue は closed issue 0084 が「既知の問題 (高レベル層で ERROR イベントが処理されない)」としてスコープ外にした課題の引き継ぎである。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_stream` は受信超過時に Error イベント (error_code 0x50) を push するのみで、セッションを閉じない
- C++ 内で close_session しない上、高レベル層 (`src/webtransport/h2/client.py` の `run()` / `src/webtransport/h2/server.py` のイベントループ) は `EventType.ERROR` を一切処理しないため、アプリにも通知されない
- ピアは制限超過データを送り続けられる (bytes_received は更新されず、データは黙って捨てられる)
- 同一の受信フロー制御違反の閉鎖経路 (0x50 で close_session) を open issue 0097 (カプセル値減少検知) も扱うため、実装順序と閉鎖機構 (close_session 直接呼び出し) の統一が必要 (Error イベント push の有無は統一しない: 0099 は継続・0097 は push なし)

## 設計方針

- **セッション閉鎖 (C++ 側)**: 受信超過検知時に Error イベント (error_code 0x50) の push を継続した上で、`close_session(session_id, 0x50, ...)` を直接呼んでセッションを閉じる。Error イベント push は高レベル層への通知経路 (後述) を担うため残す (0097 のカプセル値減少検知が「Error イベントを push せず close_session のみ」の方式でも、0099 の受信超過経路は push を継続する。0097 の経路は通知対象外)。`report_stream_state_error` は error_code 0x51 固定のため、0x50 を流すには使わない。0x50 は draft-15 Section 3.4 の 0xTBD のプレースホルダ (issue 0086 でコメント注記を対応予定。本 issue が新設する箇所にも同様の注記を付ける)。close_session は送信をキューするのみで nghttp2_session_send を呼ばないため、mem_recv コールバック内からの呼び出しは安全 (process_capsules の is_terminated ガードあり)
- **既存コメントの書き換え**: `handle_wt_stream` の「閉鎖処理はイベント化して外側で close_session する」というコメント (現在その閉鎖処理は存在しない stale コメント) を、新方式 (Error push + close_session 直接呼び出し) に合わせて書き換える。あわせて `report_stream_state_error` の「高レベル層では処理されない既知の制約」というコメントも、0099 実装後は 0x50 が `on_error` で処理されるため不正確になる。実装時に同コメントも新方式に合わせて更新する
- **高レベル層の通知**: C++ 側で close_session すると、ピアの END_STREAM 応答時に `on_session_closed` 経由でアプリには「セッションが閉じた」ことは通知される。本 issue で追加するのは、**フロー制御違反という理由を伝えるための通知** (新規コールバック `on_error` で error_code を伝える) である。C++ の Error イベントは 0x50 の他に 0x51・nghttp2 エラー・SETTINGS 違反も同一キューに流れるため、**高レベル層で error_code 0x50 のみを `on_error` に渡し、他は無視する**フィルタ方式とする (h3 側の error_code 0x0108 フィルタ (h3/client.py) と同じ方式)。セッションエラーは HTTP/2 接続状態を必ずしも変える必要がない (draft-15 Section 3.4) ため、接続終了は行わない
- **実装競合の注意**: 同一経路を変更する issue 0097 (カプセル値減少で 0x50 close_session)・0086 (0x50 プレースホルダ注記) との実装順序と方式の統一を考慮する
- テストを追加する: WT_MAX_DATA 超過・WT_MAX_STREAM_DATA 超過の両方でセッションが閉じること (Sans-IO 構成でワイヤ注入。`tests/test_webtransport_h2_stream_state_error.py` の既存パターンに従う)
- 変更内容を CHANGES.md の `## develop` に [FIX] として記載する

## 完了条件

- 受信データが WT_MAX_DATA / WT_MAX_STREAM_DATA (広告した上限値) を超えたときにセッションが閉じる (0x50 の WT_CLOSE_SESSION が送出される)
- 高レベル層で受信超過の 0x50 のエラーがアプリへ通知される (新規コールバック `on_error`。0097 のカプセル値減少経路の 0x50 は通知対象外)
- ピアが制限超過データを送り続けられない
- テストが追加され、全テストが通る
