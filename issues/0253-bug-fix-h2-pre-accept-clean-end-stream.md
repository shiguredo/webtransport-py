# h2 で受理前のクリーン END_STREAM がセッション終了として検知されない

- Created: 2026-09-23
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-pre-accept-clean-end-stream
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 3.4 は「WebTransport セッションは、CONNECT リクエストを開始したストリームをどちらかの endpoint が閉じた時点で終了する」と定める。サーバーが 2xx を送出する前にピアが WT_CLOSE_SESSION 無しの END_STREAM で CONNECT ストリームを閉じた場合 (以下、受理前 END_STREAM)、`H2Session::handle_end_stream` の確立判定で早期 return し、セッション終了が検知されない。

この結果、後から `accept_session` してもセッションは生存扱いのまま残り、`SessionClosed` が発火しない。終了済みセッションへのデータグラム / ストリーム送信が継続でき、エントリと受信記録は接続終了まで残る。

WT-H3 は受理前 FIN の検知と遅延クローズを実装済み (0058 / 0064 / 0065) であり、WT-H2 側だけ非対称が残っている。closed 0070 は「サーバー側の受理前 FIN は検知対象外となり、エントリが残留する (h3 側の受理前 FIN 対応の h2 版は本 issue のスコープ外)」として先送りした。

## 現状

- `H2Session::handle_end_stream` は冒頭で `!wt_session || !wt_session->is_established || wt_session->is_terminated` を早期 return する。受理前 END_STREAM ではこの条件に該当し、`capsule_buffer` の切り詰め検証もセッション終了イベントも行われない。呼び出し元は `on_frame_recv_callback` の `NGHTTP2_FLAG_END_STREAM` 判定であり、CONNECT + END_STREAM が同一フレームで届く場合も、HEADERS の後に別フレームで届く場合も同じ経路になる
- `WtSessionInfo` に受理前 END_STREAM を記録する場所が無い。`H2Session::accept_session` は 2xx を送出し、受理前に蓄積したカプセルを `H2Session::process_capsules` で遅延処理するが、受理前 END_STREAM の情報が無いため終了処理に到達しない
- 受理後の END_STREAM は `H2Session::handle_end_stream` が `SessionClosed` (error_code 0) を push してエントリを破棄する (closed 0070)。WT_CLOSE_SESSION 受信経路は `H2Session::handle_wt_close_session` が `SessionClosed` を push し、END_STREAM 応答 (Section 6.12 の受信者 MUST) を送出する。受理前 END_STREAM はどちらの経路にも乗らない
- `tests/test_webtransport_h2_end_stream.py` の `test_end_stream_pre_accept_truncated_capsule_no_termination` が「受理前の END_STREAM では SessionClosed が発火しない」ことを既知のギャップとしてピン留めしている (検査を早期 return より前に置く誤実装の検出が目的であり、本 issue の修正で期待値の書き換えが必要)

## 設計方針

- 検知は `H2Session::handle_end_stream` の早期 return 分岐で行う。サーバー側 (`is_server_`) でエントリが存在し、`!is_established` かつ `!is_terminated` の場合を受理前 END_STREAM とし、新規の保留集合 (`pending_pre_accept_end_stream_session_ids_` 等) に記録する。クライアント側は対象外とする (2xx 前にサーバーが END_STREAM で CONNECT ストリームを閉じるのは異常系であり、h3 の受理前 FIN 対応もサーバー側限定)
- 検知時点ではエントリを削除しない。`accept_session` が 2xx を送出し、受理前に蓄積したカプセルを遅延処理できる必要がある。ピアの END_STREAM 後も自側の応答は可能であり (HTTP/2 の half-closed (remote))、受理前 END_STREAM は「2xx を返してから終了する」受理前 FIN の意味論に合わせる
- `is_terminated` は検知に使わない。`H2Session::process_capsules` が `is_terminated` で蓄積を破棄するため、受理前の楽観的カプセルの遅延処理 (closed 0157) を壊す。終了状態は保留集合で別に持つ
- 終了処理は `accept_session` の遅延カプセル処理の後に置く。保留集合のセッションがまだ存在し `is_terminated` でなければ、`SessionClosed` (error_code 0) を push し、`wt_sessions_` / `http2_stream_buffers_` / 受信バイト記録を破棄する。error_code 0 は「WT_CLOSE_SESSION 無しのクリーンクローズは error code 0 の WT_CLOSE_SESSION と等価」という受理後 END_STREAM と同じ意味論にする
- 遅延カプセル処理との整合を定める。WT_CLOSE_SESSION の処理で既に閉じた場合は二重発火させない (`is_terminated` とエントリ不在で判定)。受理前バッファに切り詰めが残る場合は `H2Session::handle_end_stream` と同じく PROTOCOL_ERROR のストリームエラー (RFC 9297 Section 3.3) として扱い、clean な `SessionClosed` を発火させない
- 応答 END_STREAM は送出しない。受理後の END_STREAM 検知と同じ扱いであり、Section 6.12 の受信者 MUST は WT_CLOSE_SESSION 無しの END_STREAM には適用されない。ストリームは half-closed (remote) のまま接続終了まで残る (受理後経路と揃える)
- 検知後は終了を学習した状態として `send_datagram` / `send_stream_data` のガードに保留集合を加える (accept 前に低レベル Sans-IO で送信する経路を塞ぐ)。`open_stream` は受理前は `is_established` が false のため既に失敗する
- 保留集合はセッションのエントリを削除する全経路 (`reject_session` / `handle_wt_close_session` / `on_stream_close_callback` / `close_session` / 受理後 END_STREAM) で除去し、残留させない
- 変更対象: `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h`、`tests/test_webtransport_h2_end_stream.py` (既存ピンの書き換えと追加)。受理前蓄積との組み合わせが必要なら `tests/test_webtransport_h2_datagram.py` にも追加する
- `CHANGES.md` は現時点では変更しない (CODEBASE.md の指示)。`skills/webtransport-py/SKILL.md` は高レベル `h2.Server` のコールバック順序に影響する場合のみ更新する

## 完了条件

- サーバーが 2xx を送出する前にピアが WT_CLOSE_SESSION 無しの END_STREAM で CONNECT ストリームを閉じると、`accept_session` 後に `SessionClosed` (error_code 0) が 1 回だけ発火し、セッションのエントリが削除される (`get_session_ids()` から消える)
- CONNECT + END_STREAM が同一フレームの場合と、HEADERS の後に別フレームで END_STREAM が届く場合の両方で検知できる
- 2xx は従来どおり送出され、クライアントはセッション確立を認識できる (受理前 END_STREAM でも応答可能性を損なわない)。高レベル `h2.Server` では `on_session_ready` の後に `on_session_closed` が 1 回呼ばれる
- 検知後は終了扱いになり、データグラム / ストリーム送信が送出されない
- 受理後 END_STREAM (closed 0070) と WT_CLOSE_SESSION (closed 0074) の経路は影響を受けず、`SessionClosed` が二重に発火しない。受理前バッファに切り詰めが残る場合は clean な `SessionClosed` ではなく PROTOCOL_ERROR のストリームエラーになる
- 検知の追加を外すとテストが失敗することを実測で確認する (RED)
- モックなしの Sans-IO 構成 (END_STREAM フラグ付き DATA / HEADERS のワイヤ注入) で検証できる
- 全テストが通過する

## 対象外

- 受理後 END_STREAM / WT_CLOSE_SESSION 経路の応答 END_STREAM の扱い変更 (ストリームが half-closed (remote) のまま残る既知の制約の解消)
- データストリームのピア FIN を高レベル API へ通知する対応 (0220 で扱う)
