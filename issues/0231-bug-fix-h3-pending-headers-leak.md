# WebTransport over HTTP/3 の受信ヘッダーバッファがストリーム終了で解放されない

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-pending-headers-leak
- Polished: {YYYY-MM-DD}

## 目的

`src/bindings/webtransport_h3.cpp` の `H3Session` が持つ `pending_headers_` は、QPACK デコードブロック中のストリームが終了しても解放されない。WebTransport over HTTP/3 の本番経路は `H3Session` であり、この残留はアプリの利用経路で発生する。

## 現状

- `H3Session::pending_headers_` は `H3Session::begin_headers_cb` で作られ、削除は `H3Session::end_headers_cb` と `H3Session::end_trailers_cb` のみである
- `H3Session::stream_close_cb` は `forget_pre_accept_stream` と `stream_info_` の削除だけを行い `pending_headers_` に触れていない。`H3Session::close_stream` は `pending_qpack_blocked_fin_stream_ids_` と `headers_guards_` は削除するが `pending_headers_` は削除しない
- QPACK デコードブロック中は `begin_headers_cb` が発火済みで `end_headers_cb` が未発火のまま `pending_headers_` にエントリが残る (実装のコメントが「`pending_headers_` に含まれる (begin_headers_cb 発火済み・end_headers_cb 未発火)」「ブロック中にリセットされたストリームの記録は close_stream で除去する」としている状態である)
- この状態でピアが `STREAM_RESET` を送ると `src/webtransport/h3/server.py` が `webtransport_session.close_stream` を呼ぶため、`pending_headers_` のエントリが接続終了まで残る
- `src/bindings/http3.cpp` の `Http3Connection::pending_headers_` にも同型の残留があるが、対象が別コンテナのため別 issue で扱う

## 設計方針

- `H3Session::close_stream` と `H3Session::stream_close_cb` の両方で `pending_headers_` のエントリを削除する。QPACK デコードブロック中は `stream_close_cb` が到達しない経路があるため、`close_stream` 側にも削除を置く
- `H3Session::reset_stream_cb` にも同じ削除を置く。nghttp3 が内部でストリームを中断する経路 (セッション終了に伴う残留データストリームの破棄など) では `close_stream` を通らずここに到達する
- 本 issue は `pending_headers_` の解放のみを対象とする。保持量の上限は扱わない

## 完了条件

- QPACK デコードブロック中にストリームが終了したとき、`H3Session::pending_headers_` のエントリが削除される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `H3Session::close_stream`、`H3Session::stream_close_cb`、`H3Session::reset_stream_cb` に `pending_headers_.erase(stream_id)` を追加する
- `H3Session` にテスト専用の観測 API を追加して `pending_headers_` の残留を観測できるようにする
- `src/webtransport/webtransport_ext/h3.pyi` を再生成して追跡分を更新する
- `tests/` に QPACK デコードブロック中にリセットする経路のテストを追加する (`tests/test_webtransport_h3_qpack_blocked_pre_accept_fin.py` の構成に倣う)
