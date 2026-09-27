# h2 で END_STREAM のみのセッション終了に応答 END_STREAM を送出しない

- Created: 2026-09-23
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-clean-close-end-stream-response
- Polished: 2026-09-27

## 目的

draft-ietf-webtrans-http2-15 Section 6.12 (`refs/webtrans/draft-ietf-webtrans-http2-15.txt`) は「WT_CLOSE_SESSION 無しのクリーンな終了は error code 0 かつ空のエラー文字列の WT_CLOSE_SESSION による終了と等価」と定め、WT_CLOSE_SESSION の受信者 MUST として「カプセルの受信時に END_STREAM フラグ付きの HTTP/2 フレームで応答してストリームを閉じること」を定める。後者の MUST は文面上はカプセルの受信を条件とするため、等価規定を通じて END_STREAM のみの受信にも及ぶかは解釈が分かれる。closed 0070 は応答 END_STREAM を送出せずストリームが half-closed (remote) のまま残ることを既知の制約として残し、closed 0253 は「応答 END_STREAM は送出しない。受理後の END_STREAM 検知と同じ扱いであり、Section 6.12 の受信者 MUST は WT_CLOSE_SESSION 無しの END_STREAM には適用されない」として受理後経路と揃え、その対象外に「受理後 END_STREAM / WT_CLOSE_SESSION 経路の応答 END_STREAM の扱い変更 (ストリームが half-closed (remote) のまま残る既知の制約の解消)」を残している。本 issue はこの対象外を回収する。

しかし h2 の END_STREAM のみの終了経路は、受理後 (`H2Session::handle_end_stream`) も受理前 (`H2Session::terminate_pre_accept_end_stream_session`) も END_STREAM 応答を送出しない。ストリームは half-closed (remote) のまま接続終了まで残り (HTTP/2 でストリームが解放されるのは両ハーフが閉じたとき。RFC 9113 Section 5.1)、同時ストリーム枠 (SETTINGS_MAX_CONCURRENT_STREAMS) を消費し続け、ピアはセッションのクローズ完了を学習できない。h3 は受理前 FIN の遅延クローズで `close_stream` を呼び、高レベル `h3.Server` も SESSION_CLOSED で応答 FIN を送って終了のハンドシェイクを完了しており (応答しないとピアの接続終了がハングする)、h2 だけ非対称が残っている。

本 issue はこの非対称と同時ストリーム枠の解放を根拠に、0070 / 0253 の解釈を実装ポリシーとして変更し、応答 END_STREAM を送出する。変更後のコメントは「仕様の MUST 違反の修正」ではなく「等価規定の下で h3 と揃えてリソースを解放する実装ポリシー」として書く。

## 現状

- `H2Session::handle_end_stream` (受理後 END_STREAM) は `SessionClosed` を push してエントリとバッファを破棄するが、`end_stream_pending_` を立てないため応答 END_STREAM が送出されない
- `H2Session::terminate_pre_accept_end_stream_session` (受理前 END_STREAM) も同じ
- 一方、WT_CLOSE_SESSION 受信経路 (`H2Session::handle_wt_close_session`) は `end_stream_pending_` + `nghttp2_session_resume_data` で END_STREAM 応答を送出し、ストリームを両ハーフクローズで閉じる (closed 0074)
- 応答はデータプロバイダ付きで送出済みのため、`end_stream_pending_` を立てて `nghttp2_session_send` が走れば空 DATA + END_STREAM が送出できる (受理前は受理の 2xx 送出後)

## 設計方針

- `H2Session::handle_end_stream` のクリーン終了経路と `H2Session::terminate_pre_accept_end_stream_session` のクリーン終了経路で、`handle_wt_close_session` と同じく `end_stream_pending_` にセッション ID を追加し、`nghttp2_session_resume_data` で応答の END_STREAM を送出する。エントリとバッファの破棄・`SessionClosed` の push は現状のまま先に行い、エントリ削除済みのため `on_stream_close_callback` による二重発火は起きない。受理前経路の `terminate_pre_accept_end_stream_session` は `accept_session` の 2xx 送出後に呼ばれるが、同じ位置から呼ばれる `handle_wt_close_session` (受理前に蓄積した WT_CLOSE_SESSION の遅延処理) が同じ 2 操作で応答 END_STREAM を送出しており、その挙動は `tests/test_webtransport_h2_end_stream.py` の `test_end_stream_pre_accept_wt_close_session_and_end_stream_single_fire` で固定されている
- `H2Session::handle_end_stream` は `on_frame_recv_callback` から呼ばれるため `nghttp2_session_send` は呼ばない。送出は呼び出し元 (`receive()` 末尾の `nghttp2_session_send` または `send()`) に委ねる (既存の WT_CLOSE_SESSION 経路と同じ)
- 受理前の経路は `accept_session` の遅延カプセル処理後に終了処理するため、END_STREAM の実送出は `accept_session` の外 (高レベル層の `send()`) になる。0254 の送信タイミングの扱いと整合させる
- 変更対象: `src/bindings/webtransport_h2.cpp` の `H2Session::handle_end_stream` の実装と、`H2Session::handle_end_stream` / `H2Session::terminate_pre_accept_end_stream_session` にある「ピアの END_STREAM に対する自側の応答 (END_STREAM 送出) は行わない」「ストリームは half-closed (remote) のまま接続終了まで残る既知の制約」「Section 6.12 の受信者側 MUST は WT_CLOSE_SESSION 受信時の応答についての規定であり、END_STREAM のみの受信には該当しない」の書き換え、および `H2Session::handle_wt_close_session` にある「handle_end_stream の経路は自側が END_STREAM を送らない点が異なる」の書き換え (実装ポリシー変更として書く)。`src/bindings/webtransport_h2.h` は `H2Session::end_stream_pending_` のメンバーコメント「close_session 後に END_STREAM を送るストリーム」が実態と合わなくなるため更新する。`tests/test_webtransport_h2_end_stream.py` に受理後 / 受理前で応答 END_STREAM が送出されることの表明を追加し、`test_end_stream_server_pre_accept_end_stream_after_headers_terminates_after_accept` の `client.get_session_ids() == [session_id]` の表明を更新する (応答 END_STREAM により、クライアントは 2xx の確立直後に `SessionClosed` を 1 回発火してセッションが消えるため)。`tests/test_webtransport_h2_stop_sending_drain_session.py` の `test_stop_sending_after_peer_end_stream_not_sent` の docstring「エントリ削除後も自側の END_STREAM 応答は送出されない (既知の制約)」を更新する。docstring を変える場合は `src/webtransport/webtransport_ext/h2.pyi` も再生成する
- `CHANGES.md` は現時点では変更しない (CODEBASE.md の指示)

## 完了条件

- 受理後 END_STREAM と受理前 END_STREAM のどちらでも、応答の空 DATA + END_STREAM がワイヤへ送出され、自側のストリームが両ハーフクローズで閉じる (`send()` の戻り値に `tests/conftest.py` の `_encode_data_frame(session_id, end_stream=True)` が現れることで表明する。受理前経路の実送出は `accept_session` の後に呼ぶ `send()` になるため、`accept_session` の直後ではなくその次の `send()` で表明する)。ピア側では、2xx の受信で確立した後にこの応答 END_STREAM を検知して `SessionClosed` が 1 回発火する (ワイヤ注入で作ったピアでも、確立済みセッションがピアの END_STREAM を検知する経路は `test_end_stream_only_closes_session` と同じ)
- `SessionClosed` は自側・ピア側とも 1 回だけで、二重発火しない
- WT_CLOSE_SESSION 経路 (closed 0074) の END_STREAM 応答と二重送出にならない
- 切り詰め (PROTOCOL_ERROR) 経路と非 2xx 拒否経路は影響を受けない
- 応答 END_STREAM の送出処理を外すとテストが失敗することを実測で確認する (RED)
- モックなしの Sans-IO 構成で検証できる
- 全テストが通過する

## 対象外

- 受理前 END_STREAM の切り詰め終了の通知タイミング (0254 で扱う)
- 受理前 END_STREAM の保留状態の設計変更 (0256 で扱う)
