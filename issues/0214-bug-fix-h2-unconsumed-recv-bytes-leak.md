# h2 の未消費受信バイト記録がストリーム終了で解放されない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-unconsumed-recv-bytes-leak
- Polished: {YYYY-MM-DD}

## 目的

`src/bindings/webtransport_h2.cpp` の `H2Session` が持つ `unconsumed_recv_bytes_` は、ストリームが終了しても解放されない。長命な HTTP/2 コネクションで多数のストリームを処理するとエントリが単調増加する。

## 現状

- `unconsumed_recv_bytes_` へ加算するのは `H2Session::on_data_chunk_recv_callback` のみ
- 減算と削除を行うのは `H2Session::consume_recv_bytes` のみで、削除は残量が 0 になったときに限られる
- `H2Session::on_stream_close_callback` は `http2_stream_buffers_` と `end_stream_pending_` を削除するが、`unconsumed_recv_bytes_` には触れていない
- 残量が 0 にならないままストリームが閉じる経路がある
  - 受理前の楽観的カプセルを上限超過で 413 拒否する経路 (`H2Session::reject_session` を呼ぶ分岐) は、加算後にセッションのエントリを消すため後続チャンクは早期 return し、記録だけが残る
  - クライアント側で 2xx 応答前に受信したデータは消費されない
  - 受信の途中でストリームがリセットされた場合、未完成カプセルの分が残る
- 同種のストリーム単位コンテナは終了時に確実に削除されている (`src/bindings/quic.cpp` の `QuicConnection::stream_close_cb` と `stream_reset_cb` が `stream_unacked_` と `stream_acked_offset_` を削除する)

## 設計方針

- ストリームが終了するすべての経路で `unconsumed_recv_bytes_` のエントリを削除する
- 413 拒否の経路では、ウィンドウを戻す必要が無いため削除のみでよい
- 削除漏れが再発しないよう、ストリーム単位コンテナの後始末を 1 箇所へ寄せることも検討する。ただし他コンテナの扱いを変える場合はイベント列に差が出ないことをテストで確認する

## 完了条件

- ストリーム終了、セッション終了、413 拒否のいずれの経路でも `unconsumed_recv_bytes_` のエントリが残らない
- 長命コネクションで多数のストリームを処理してもエントリ数が増えないことを検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_stream_close_callback` に `unconsumed_recv_bytes_` の削除を追加する
- `H2Session::handle_end_stream`、`H2Session::handle_wt_close_session`、`H2Session::reject_session`、`H2Session::close_session` の各終了経路でも同じ削除を行う
- テスト専用の観測 API (`_test_*`) を追加するか、既存の `tests/test_webtransport_h2_recv_flow_control.py` の検証方法に合わせてエントリの残留を観測できるようにする
