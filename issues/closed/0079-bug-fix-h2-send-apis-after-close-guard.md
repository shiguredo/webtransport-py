# HTTP/2 の stop_sending / drain_session が終了済みセッション宛にカプセルを送出・残留させる問題を修正する

- Created: 2026-08-14
- Completed: 2026-08-15
- Branch: feature/fix-h2-send-apis-after-close-guard
- Polished: 2026-08-15

## 目的

HTTP/2 の `stop_sending` / `drain_session` は `get_wt_session` を確認せず `send_capsule` を呼ぶため、セッション終了後に呼ぶと、終了済みセッション宛にカプセルをワイヤへ送出してしまう (ピアの END_STREAM 受信経路・ローカル `close_session` 後の flush 前) か、`http2_stream_buffers_` に残留させてメモリを保持し続ける (WT_CLOSE_SESSION 受信経路)。エントリ不在・終了済み (`is_terminated`) の両方を確認して塞がる `send_datagram` と揃えて no-op にする。

## 現状

- `src/bindings/webtransport_h2.cpp` の `stop_sending` と `drain_session` は `get_wt_session` を確認せず `send_capsule` する (エントリ不在・終了済みでもカプセルをキューする)
- セッション終了後の挙動は経路によって異なる:
  - WT_CLOSE_SESSION 受信経路: `handle_wt_close_session` がエントリを削除し END_STREAM 応答を送出するため、以後の送出は行われない。`stop_sending` / `drain_session` を呼ぶと消えた `http2_stream_buffers_` エントリが再生成され、カプセルはワイヤに送出されず残留する (ストリームが閉じているため以後も破棄されず、メモリを保持し続ける)
  - ピアの END_STREAM 受信経路: `handle_end_stream` はエントリを削除するが自側の END_STREAM 応答は送出しない (既知の制約) ためストリームは half-closed (remote) で生存し、`nghttp2_session_resume_data` は成功する。カプセルはワイヤに送出されてしまう (終了済みセッション宛の誤送出)
  - ローカル `close_session` 後: エントリは残存したまま `is_terminated` のみ立つ。`get_wt_session` の確認だけでは塞がれず、flush 前はカプセルが WT_CLOSE_SESSION の後ろに積まれて送出され、flush 後は残留する
- 終了処理ハンドラ (`handle_wt_close_session` / `handle_end_stream`) とクライアント側の非 2xx 応答処理 (`on_frame_recv_callback`) のコメントでは「stop_sending / drain_session は get_wt_session を確認せずカプセルを送出するため塞がれないが、対象外」と明記されている。`reject_session` のコメントも「終了経路の stop_sending / drain_session と同じ扱い」として滞留を許容している
- 他の送信 API (`send_datagram` / `send_stream_data` / `open_stream` / `reset_stream` / `close_session`) は `get_wt_session` の確認でエントリ不在時に塞がれる。`send_datagram` はさらに `is_terminated` も確認する

## 設計方針

- `stop_sending` / `drain_session` の冒頭に `get_wt_session` と `is_terminated` の確認を追加し、エントリ不在時・終了済み時は no-op にする (`send_datagram` と同一のガード。終了の検知は `send_datagram` と同じ構成: WT_CLOSE_SESSION 受信後・ピアの END_STREAM 受信後はエントリ削除で、ローカル `close_session` 後は `is_terminated` で塞ぐ)
- 終了処理ハンドラ (`handle_wt_close_session` / `handle_end_stream`)・クライアント側の非 2xx 応答処理 (`on_frame_recv_callback`)・`reject_session` のコメントの「対象外」「同じ扱い」の記述を更新する (塞がれるようになるため)。`send_datagram` の実装コメント (ガードを `send_capsule` に置かない理由で `stop_sending` / `drain_session` を挙げている箇所) も、自前ガードを持つようになる点を反映して見直す
- テスト: セッション終了後に `stop_sending` / `drain_session` を呼んでもワイヤに送出されないことを検証する。`http2_stream_buffers_` への残留は内部状態のため公開 API からは観測できない (既存テストの docstring も同旨)。修正の検証は挙動に差が出る経路で行う:
  - ピアの END_STREAM 受信経路: 修正前は送出される → 修正後は送出されない (ワイヤ検証で修正を確認できる)
  - ローカル `close_session` 後: 修正前は flush 前に送出され得る → 修正後は送出されない
  - クライアントの非 2xx 拒否受信経路: 修正前は送出される (エントリ削除後もストリームは生存) → 修正後は送出されない
  - WT_CLOSE_SESSION 受信経路: 修正前後とも送出されない (送出なしのピン)
  - サーバー側の `reject_session` 2xx 送出経路は修正前後とも送出されないためテスト対象外
- 変更対象: `src/bindings/webtransport_h2.cpp` (ガード追加・コメント更新) / `src/bindings/webtransport_h2.h` (`stop_sending` / `drain_session` の docstring に「終了済みセッション ID への送信は無視される」旨を追記) / テスト / `CHANGES.md` (## develop セクションへの [FIX] エントリ)。高レベル API (`src/webtransport/h2/client.py` / `server.py`) に `stop_sending` / `drain_session` は存在しないため変更不要

## 完了条件

- セッション終了後 (WT_CLOSE_SESSION 受信 / ピアの END_STREAM 受信 / ローカル `close_session` 後) に `stop_sending` / `drain_session` を呼んでもカプセルがワイヤに送出されない (no-op)
- 全テストが通る

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `stop_sending` / `drain_session` の冒頭に `get_wt_session` + `is_terminated` の確認を追加し、エントリ不在時・終了済み時は no-op にした (`send_datagram` と同一のガード構成)
- 終了処理ハンドラ (`handle_wt_close_session` / `handle_end_stream`)・クライアント側の非 2xx 応答処理 (`on_frame_recv_callback`)・`reject_session` のコメントを「stop_sending / drain_session もエントリ不在・終了済みで塞がれる」内容に更新した。`send_datagram` の実装コメントも自前ガードを持つ点を反映して見直した
- `src/bindings/webtransport_h2.h` の `stop_sending` / `drain_session` / `reject_session` の docstring に「終了済みセッション ID への送信は無視される」旨を追記した
- `tests/test_webtransport_h2_stop_sending_drain_session.py` を新規作成し、テスト 12 本を追加した (ピアの END_STREAM 受信後・ローカル `close_session` 後 (flush 前)・クライアントの非 2xx 拒否受信後に送出されないことの検証 6 本、WT_CLOSE_SESSION 受信後の送出なしピン 2 本、生存セッションの回帰ピン 2 本、未 connect ID の無害性確認 2 本)。ガード無効化ビルドで 6 本失敗することを確認し、修正の検証として機能することを実証した
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
- 全テスト (634 本) が通ることを確認した
