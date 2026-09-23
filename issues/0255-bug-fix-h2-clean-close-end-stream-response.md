# h2 で END_STREAM のみのセッション終了に応答 END_STREAM を送出しない

- Created: 2026-09-23
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-clean-close-end-stream-response
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http2-15 Section 6.12 は「WT_CLOSE_SESSION 無しのクリーンな終了は error code 0 の WT_CLOSE_SESSION と等価」と定め、WT_CLOSE_SESSION 受信時の受信者 MUST は「END_STREAM フレームで応答してストリームを閉じること」である。しかし h2 の END_STREAM のみの終了経路は、受理後 (`H2Session::handle_end_stream`) も受理前 (`H2Session::terminate_pre_accept_end_stream_session`) も END_STREAM 応答を送出しない。ストリームは half-closed (remote) のまま接続終了まで残り、同時ストリーム枠が解放されず、ピアはセッションのクローズ完了を学習できない。

h3 は受理前 FIN の遅延クローズで `close_stream` を呼び、高レベル `h3.Server` も SESSION_CLOSED で応答 FIN を送って終了のハンドシェイクを完了しており、h2 だけ非対称が残っている。closed 0070 は「ピアの END_STREAM に対する自側の応答 (END_STREAM 送出) は行わない (ストリームは half-closed (remote) のまま接続終了まで残る既知の制約)」として先送りしている。

## 現状

- `H2Session::handle_end_stream` (受理後 END_STREAM) は `SessionClosed` を push してエントリとバッファを破棄するが、`end_stream_pending_` を立てないため応答 END_STREAM が送出されない
- `H2Session::terminate_pre_accept_end_stream_session` (受理前 END_STREAM) も同じ
- 一方、WT_CLOSE_SESSION 受信経路 (`H2Session::handle_wt_close_session`) は `end_stream_pending_` + `nghttp2_session_resume_data` で END_STREAM 応答を送出し、ストリームを両ハーフクローズで閉じる (closed 0074)
- 応答はデータプロバイダ付きで送出済みのため、`end_stream_pending_` を立てて `nghttp2_session_send` が走れば空 DATA + END_STREAM が送出できる (受理前は受理の 2xx 送出後)

## 設計方針

- `H2Session::handle_end_stream` のクリーン終了経路と `H2Session::terminate_pre_accept_end_stream_session` のクリーン終了経路で、`handle_wt_close_session` と同じく `end_stream_pending_` にセッション ID を追加し、`nghttp2_session_resume_data` で応答の END_STREAM を送出する。エントリとバッファの破棄・`SessionClosed` の push は現状のまま先に行い、エントリ削除済みのため `on_stream_close_callback` による二重発火は起きない
- `H2Session::handle_end_stream` は `on_frame_recv_callback` から呼ばれるため `nghttp2_session_send` は呼ばない。送出は呼び出し元 (`receive()` 末尾の `nghttp2_session_send` または `send()`) に委ねる (既存の WT_CLOSE_SESSION 経路と同じ)
- 受理前の経路は `accept_session` の遅延カプセル処理後に終了処理するため、END_STREAM の実送出は `accept_session` の外 (高レベル層の `send()`) になる。0254 の送信タイミングの扱いと整合させる
- 変更対象: `src/bindings/webtransport_h2.cpp` / `.h` (「応答 END_STREAM は送出しない」「half-closed (remote) のまま残る既知の制約」のコメント更新)、`tests/test_webtransport_h2_end_stream.py` (受理後 / 受理前で END_STREAM 応答が送出され、ピア側の `SessionClosed` が 1 回発火することの追加と既存テストの表明更新)。docstring を変える場合は `src/webtransport/webtransport_ext/h2.pyi` も再生成する
- `CHANGES.md` は現時点では変更しない (CODEBASE.md の指示)

## 完了条件

- 受理後 END_STREAM と受理前 END_STREAM のどちらでも、応答の空 DATA + END_STREAM が送出され、ストリームが両ハーフクローズで閉じる (ピア側でも `SessionClosed` が 1 回発火する)
- `SessionClosed` は自側・ピア側とも 1 回だけで、二重発火しない
- WT_CLOSE_SESSION 経路 (closed 0074) の END_STREAM 応答と二重送出にならない
- 切り詰め (PROTOCOL_ERROR) 経路と非 2xx 拒否経路は影響を受けない
- 応答 END_STREAM の送出処理を外すとテストが失敗することを実測で確認する (RED)
- モックなしの Sans-IO 構成で検証できる
- 全テストが通過する

## 対象外

- 受理前 END_STREAM の切り詰め終了の通知タイミング (0254 で扱う)
- 受理前 END_STREAM の保留状態の設計変更 (0256 で扱う)
