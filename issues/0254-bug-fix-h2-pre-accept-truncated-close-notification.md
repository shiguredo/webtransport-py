# h2 で受理前 END_STREAM の切り詰め終了が send() を呼ぶまで通知されない

- Created: 2026-09-23
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-pre-accept-truncated-close-notification
- Polished: 2026-09-27

## 目的

h2 で、受理前 END_STREAM を検知したセッションに切り詰めが残る場合、`H2Session::terminate_pre_accept_end_stream_session` は `reset_stream_for_malformed_capsule` で RST_STREAM を submit するだけで、`SessionClosed` は RST_STREAM の送出で発火する `on_stream_close_callback` が push する。そのため `H2Session::accept_session` から戻った時点ではイベントが未発火であり、通知は呼び出し元の `send()` (`nghttp2_session_send`) に依存する。

高レベル `h2.Server` の受信ループ (`src/webtransport/h2/server.py` の `_handle_client`) は、読み取りが EOF を返すとイベント drain の前に `break` するため、ピアがサーバーの 2xx + RST_STREAM を受信した直後に TCP 接続を閉じると、RST_STREAM の送出で `on_stream_close_callback` が push した `SessionClosed` が drain されず `on_session_closed` が呼ばれない窓がある (RST_STREAM の送出は同じ周回の `send()`、イベントの drain は次の周回になる)。クリーンな受理前 END_STREAM は `accept_session` 内で `SessionClosed` を push するため影響しない (非対称になっている)。切り詰め分岐でも `accept_session` の中で RST_STREAM を送出して `SessionClosed` を push すれば、同じ周回の drain で通知が確定し、この窓も閉じる。

## 現状

- `H2Session::terminate_pre_accept_end_stream_session` の切り詰め分岐は `reset_stream_for_malformed_capsule` を呼んで return し、`SessionClosed` を直接 push しない
- `H2Session::reset_stream_for_malformed_capsule` は `nghttp2_submit_rst_stream` で RST_STREAM をキューするだけで `nghttp2_session_send` を呼ばない (`on_data_chunk_recv_callback` → `process_capsules` → `read_capsule_varint` / `verify_capsule_payload_fully_read` の経路で nghttp2 コールバック内からも到達するため、この関数内では `nghttp2_session_send` を呼べない)
- `accept_session` は 2xx 送出のための `nghttp2_session_send` を遅延カプセル処理より前に実行済みであり、終了処理で submit された RST_STREAM はその後送出されない
- 高レベル `h2.Server` の受信ループは `received` が空 (EOF) の場合にイベント drain を行わず `break` する
- 受理後 END_STREAM の切り詰め経路 (`H2Session::handle_end_stream` → `reset_stream_for_malformed_capsule`) も同じ構造であり、nghttp2 コールバック内から呼ばれるため `session_send` を足せない

## 設計方針

- `terminate_pre_accept_end_stream_session` の切り詰め分岐 (`if (!wt_session->capsule_buffer.empty())` ブロック) の中で、`reset_stream_for_malformed_capsule` の直後に `nghttp2_session_send(session_)` を呼び、そのまま `return` する (ブロックの外や関数末尾には置かない: クリーン分岐に足すと 0255 が決めた「応答 END_STREAM は呼び出し元の `send()` で送出する」設計と観測点がずれる)。この関数は `accept_session` (nghttp2 コールバック外) からのみ呼ばれるため再入にならない。RST_STREAM の送出と同時に `on_stream_close_callback` が `SessionClosed` を push し、`accept_session` から戻る時点で通知が確定する
- クリーン分岐と対称になるのは通知の確定タイミングである。送出タイミングは非対称のまま (クリーン分岐は 0255 のとおり呼び出し元の `send()`、切り詰め分岐は本 issue の `nghttp2_session_send`)。クリーン分岐には `nghttp2_session_send` を足さない (公開 API の `send()` は送出済みのバッファを返すだけのため、この逸脱はテストの表明で固定できない。意図は実装コメントに残す)
- 高レベル `h2.Server` は `accept_session` をイベント drain ループの中で呼び、直後にイベントを drain するため、通知が `accept_session` 内で確定すれば EOF を読む前に配送される (同じ周回の drain で `on_session_closed` が呼ばれる)。この経路の E2E テストはピアの切断タイミングに依存して RED を実測できないため追加せず、`accept_session` 直後の観測を RED の根拠にする (受理前クリーン END_STREAM の高レベル経路は既存の `test_h2_server_on_session_closed_after_pre_accept_end_stream` が担う)
- あわせて、高レベル `h2.Server` の受信ループが EOF で `break` する前に残イベントを drain するかは、本 issue の対象外とする。受理後 END_STREAM の切り詰め経路は `H2Session::receive` 末尾の `nghttp2_session_send` で `SessionClosed` が確定するため窓は無い。残る窓は、受理前に蓄積した不正カプセルを `accept_session` の遅延カプセル処理で検出して終了する経路 (`is_terminated` の早期 return) のように、`SessionClosed` の push が高レベルループ末尾の `send()` になる経路であり、必要なら別 issue で扱う
- 変更対象: `src/bindings/webtransport_h2.cpp` (`terminate_pre_accept_end_stream_session` の切り詰め分岐に `nghttp2_session_send` を追加し、通知タイミングと再入の前提をコメントで更新する)、`src/bindings/webtransport_h2.h` (`terminate_pre_accept_end_stream_session` が `nghttp2_session_send` を呼ぶため、nghttp2 コールバック外 (`accept_session` 経由) からのみ呼ぶことをコメントで明記する)、`tests/test_webtransport_h2_end_stream.py` (`accept_session` 直後に `SessionClosed` が観測できること、およびエントリ削除 (`get_session_ids()` が空) と受信バイト記録の解放が `accept_session` 内で完了することを固定する。既存の切り詰めテストは `send()` を呼ぶ前提のため、`send()` 前の観測を追加する)
- `CHANGES.md` は現時点では変更しない (CODEBASE.md の指示)

## 完了条件

- 受理前 END_STREAM + 切り詰めで `accept_session` を呼んだ直後に `SessionClosed` (error_code は PROTOCOL_ERROR) が 1 回発火し、`server.send()` を待たずに観測できる
- `accept_session` の直後に `get_session_ids()` が空であり、受信バイト記録 (`_test_unconsumed_recv_bytes`) が解放されている (エントリ削除と記録の解放が `accept_session` 内で完了する)
- 2xx HEADERS → RST_STREAM のワイヤ順序は変わらない (2xx は `accept_session` 内の `nghttp2_session_send` で送出され、RST_STREAM は `accept_session` 直後に `server.send()` が返す。`send()` は残ったバッファだけを返すため、両方が同じ戻り値に含まれることは前提にしない)
- クリーンな受理前 END_STREAM は `test_end_stream_server_pre_accept_end_stream_terminates_after_accept`、受理後 END_STREAM の切り詰めは `test_end_stream_with_truncated_capsule_resets_stream` と `test_end_stream_after_accept_with_truncated_pre_accept_buffer_resets_stream`、WT_CLOSE_SESSION は `test_end_stream_pre_accept_wt_close_session_and_end_stream_single_fire` の通過で影響がないことを確認する (新規の表明は追加しない)
- 「早期 return より前に切り詰めを検査する誤実装」「検知時に終了処理する誤実装」を検出できる既存テストの表明が維持される
- 切り詰め分岐の `session_send` を外すと `accept_session` 直後の観測が失敗することを実測で確認する (RED)
- `terminate_pre_accept_end_stream_session` が `accept_session` 以外から呼ばれていないこと (nghttp2 コールバック外である前提) を保つ (grep で確認できる形にする)
- モックなしの Sans-IO 構成で検証できる
- 全テストが通過する

## 対象外

- 受理後 END_STREAM の切り詰め経路 (通知は `H2Session::receive` 末尾の `nghttp2_session_send` で確定するため、EOF 時の drain を見送っても本経路には窓が無い)
- 高レベル `h2.Server` の EOF 時の drain の見直し (残る窓は、通知が高レベルループ末尾の `send()` で push される経路。例: `accept_session` の遅延カプセル処理で受理前の不正カプセルを検出して終了する `is_terminated` の早期 return)
- 受理前 END_STREAM の保留状態の設計変更 (0256 で扱う)
