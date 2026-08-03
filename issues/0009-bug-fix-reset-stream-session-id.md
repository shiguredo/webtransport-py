# サーバー側の STREAM_RESET イベントで誤ったセッション ID が渡されるのを修正する

- Created: 2026-08-02
- Completed: YYYY-MM-DD
- Branch: feature/fix-reset-stream-session-id
- Polished: 2026-08-03

## 目的

`Server` の `on_stream_reset` コールバックが、リセットされたストリームの属するセッションの ID を正しく受け取れるようにする。現在はセッション ID 集合の先頭要素に依存しており、1 つの QUIC 接続上に複数セッションを確立した構成 (draft-ietf-webtrans-http3-16 Section 2.2 の想定どおり) では誤ったセッション ID が渡される。

## 現状

- `src/webtransport/h3/server.py` の `Server._process_quic_events` は `STREAM_RESET` イベントのたびに `H3Session.get_session_ids()` の先頭要素をセッション ID として `on_stream_reset` に渡す
- `src/bindings/webtransport_h3.cpp` の `H3Session::close_stream` は `stream_info_` からストリーム情報を削除する際にセッション ID を返さないため、Python 側でストリーム ID からセッションを復元できない
- `get_session_ids()` は `std::set` の昇順リストであり、先頭要素は最小のセッション ID になるため、同一接続上に複数セッションがあるとリセットされたストリームと無関係な ID が渡る
- 高レベル `Client` (`src/webtransport/h3/client.py`) は 1 接続 1 セッションのため、このバグは低レベル API (`quic.Connection` + `h3.Session`) で同一接続上に複数セッションを確立した場合にのみ顕在化する

## 設計方針

- `H3Session::close_stream` がリセットされたストリームのセッション ID を返すようにし、Python 側の `get_session_ids()` の先頭要素への依存をやめる
- `stream_info_` からの取り出しは `nghttp3_conn_close_stream` 呼び出しより前に行う (nghttp3 のコールバックが同期で `stream_info_` を削除するため)。`session_ids_` の確認も同様に `nghttp3_conn_close_stream` 呼び出しより前に行う。`conn_` が無い場合は -1 を返す
- `stream_info_` に該当エントリが無い場合のフォールバック:
  - CONNECT ストリームは `session_ids_` に含まれるため、ストリーム ID 自身を返す (セッション ID は CONNECT ストリーム ID そのもの。draft-ietf-webtrans-http3-16 Section 2.2。CONNECT ストリームのリセットはセッション終了の正当な経路。Section 6)。このフォールバックは現状の「CONNECT ストリームのリセット時に `session_ids_` から削除されない」挙動に依存している (将来削除する場合は本 issue の対象外として再設計する)
  - それ以外 (制御ストリーム・QPACK ストリーム・データ未受信のままリセットされたストリーム・二重リセット) は -1 を返し、server.py はそのまま `on_stream_reset` に渡す (現状の `get_session_ids()` が空のときと同じ挙動)。データ未受信のままリセットされたストリームは RESET_STREAM_AT (draft-ietf-webtrans-http3-16 Section 4.4 の MUST) に対応すれば復元可能になるが、RESET_STREAM_AT への対応は本 issue の対象外とする
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (close_stream の戻り値・宣言・nanobind バインディングと nb::sig)、`src/webtransport/h3.pyi` (close_stream の戻り値型)、`src/webtransport/h3/server.py` (STREAM_RESET ハンドラ)。`H3Session::reset_stream` は close_stream に委譲するだけで戻り値を捨てるため変更しない。`src/webtransport/h3.pyi` に既存のドリフト (reset_stream の欠落・connect の origin 引数・EventType の RESET_STREAM 等) があるが、修正は本 issue の対象外とする
- 完了条件のテストはクライアント側を低レベル API (`quic.Connection` + `h3.Session`) で構築し、サーバー側は高レベル `Server` を使って `on_stream_reset` を検証する (高レベル `Client` は 1 接続 1 セッションのため複数セッションを確立できず、バグを再現できない。`on_stream_reset` は高レベル `Server` のコールバックであるため、サーバー側を低レベル API にすると検証できない)
- 0010 (close_stream / reset_stream の送信バッファ削除) も同じ関数を変更対象とするため、実装順序によるマージの競合に注意する
- DATAGRAM 分岐 (`src/webtransport/h3/server.py`) と `send_stream_data` のフォールバック (`src/bindings/webtransport_h3.cpp`) にある同種の先頭要素依存は対象外とする

## 完了条件

- `on_stream_reset` に渡されるセッション ID が、リセットされたストリームの属するセッションの ID である (復元できない場合は -1)
- モックなしの e2e テストで検証できる (クライアント側を低レベル API で構築し、サーバー側は高レベル `Server` の `on_stream_reset` で検証する):
  - 同一 QUIC 接続上に 2 セッションを確立し、2 つ目のセッションでクライアントが開いたストリームを、サーバー側の `on_stream_data` で受信を確認してからリセットしたときに、2 つ目のセッション ID が渡される
  - データ未受信のままリセットされたストリームには -1 が渡される (旧実装では無関係なセッション ID が渡っていたケース)
  - 2 つ目のセッションの CONNECT ストリーム (最小 ID でない CONNECT) をクライアントがリセットしたときに、セッション ID (= CONNECT ストリーム ID) が渡される
- 既存のリセット系テスト (`tests/test_e2e_webtransport_h3.py` の `test_client_resets_server_stream` 等) が引き続き通り、`on_stream_reset` の `session_id` を `on_session_ready` で受け取ったセッション ID と比較する assert を含む
