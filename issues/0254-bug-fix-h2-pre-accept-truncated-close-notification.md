# h2 で受理前 END_STREAM の切り詰め終了が send() を呼ぶまで通知されない

- Created: 2026-09-23
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-pre-accept-truncated-close-notification
- Polished: {YYYY-MM-DD}

## 目的

h2 で、受理前 END_STREAM を検知したセッションに切り詰めが残る場合、`H2Session::terminate_pre_accept_end_stream_session` は `reset_stream_for_malformed_capsule` で RST_STREAM を submit するだけで、`SessionClosed` は RST_STREAM の送出で発火する `on_stream_close_callback` が push する。そのため `H2Session::accept_session` から戻った時点ではイベントが未発火であり、通知は呼び出し元の `send()` (`nghttp2_session_send`) に依存する。

高レベル `h2.Server` の受信ループ (`src/webtransport/h2/server.py` の `_handle_client`) は、読み取りが EOF を返すとイベント drain の前に `break` するため、ピアが 2xx + RST_STREAM の受信直後に接続を閉じると `on_session_closed` が呼ばれない窓がある。クリーンな受理前 END_STREAM は `accept_session` 内で `SessionClosed` を push するため影響しない (非対称になっている)。

## 現状

- `H2Session::terminate_pre_accept_end_stream_session` の切り詰め分岐は `reset_stream_for_malformed_capsule` を呼んで return し、`SessionClosed` を直接 push しない
- `H2Session::reset_stream_for_malformed_capsule` は `nghttp2_submit_rst_stream` で RST_STREAM をキューするだけで `nghttp2_session_send` を呼ばない (この関数は `on_data_chunk_recv_callback` からも呼ばれるため、呼ぶと再入になる)
- `accept_session` は 2xx 送出のための `nghttp2_session_send` を遅延カプセル処理より前に実行済みであり、終了処理で submit された RST_STREAM はその後送出されない
- 高レベル `h2.Server` の受信ループは `received` が空 (EOF) の場合にイベント drain を行わず `break` する
- 受理後 END_STREAM の切り詰め経路 (`H2Session::handle_end_stream` → `reset_stream_for_malformed_capsule`) も同じ構造であり、nghttp2 コールバック内から呼ばれるため `session_send` を足せない

## 設計方針

- `terminate_pre_accept_end_stream_session` の切り詰め分岐で、`reset_stream_for_malformed_capsule` の後に `nghttp2_session_send(session_)` を呼ぶ。この関数は `accept_session` (nghttp2 コールバック外) からのみ呼ばれるため再入にならない。RST_STREAM の送出と同時に `on_stream_close_callback` が `SessionClosed` を push し、`accept_session` から戻る時点で通知が確定する (クリーン分岐と対称になる)
- あわせて、高レベル `h2.Server` の受信ループが EOF で `break` する前に残イベントを drain するかは、本 issue の対象外とする (受理後経路の同種の窓も残るため、必要なら別 issue で扱う)
- 変更対象: `src/bindings/webtransport_h2.cpp`、`tests/test_webtransport_h2_end_stream.py` (`accept_session` 直後に `SessionClosed` が観測できることを固定する。既存の切り詰めテストは `send()` を呼ぶ前提のため、`send()` 前の観測を追加する)
- `CHANGES.md` は現時点では変更しない (CODEBASE.md の指示)

## 完了条件

- 受理前 END_STREAM + 切り詰めで `accept_session` を呼んだ直後に `SessionClosed` (error_code は PROTOCOL_ERROR) が 1 回発火し、`server.send()` を待たずに観測できる
- 2xx HEADERS → RST_STREAM のワイヤ順序と、セッションのエントリ削除は変わらない
- クリーンな受理前 END_STREAM と受理後 END_STREAM / WT_CLOSE_SESSION の経路は影響を受けない
- 「早期 return より前に切り詰めを検査する誤実装」「検知時に終了処理する誤実装」を検出できる既存テストの表明が維持される
- 切り詰め分岐の `session_send` を外すとテストが失敗することを実測で確認する (RED)
- モックなしの Sans-IO 構成で検証できる
- 全テストが通過する

## 対象外

- 受理後 END_STREAM の切り詰め経路の同種の窓 (nghttp2 コールバック内からの `session_send` は不可。高レベル層での EOF 時 drain は別途判断)
- 受理前 END_STREAM の保留状態の設計変更 (0256 で扱う)
