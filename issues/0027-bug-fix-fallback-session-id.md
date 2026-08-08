# セッション ID 集合の先頭要素に依存するフォールバックを修正する

- Created: 2026-08-04
- Completed: 2026-08-08
- Branch: feature/fix-fallback-session-id
- Polished: 2026-08-08

## 目的

セッション ID 集合の先頭要素に依存するフォールバック 2 箇所を修正する。複数セッションを確立した構成では、先頭要素 (最小のセッション ID) は送信対象のストリームと無関係なセッションになり得る。STREAM_RESET 経路の同種の問題は 0009 で修正済みだが、別経路に同じパターンが残っている。

## 現状

- `src/webtransport/h3/server.py` の `_process_webtransport_events` の DATAGRAM 分岐は、`receive_datagram` で復元した `session_id` が負の場合に `get_session_ids()` の先頭要素を `on_datagram` に渡す (集合が空の場合は 0)。この経路は正常トラフィックでは到達しない: `receive_datagram` は Quarter Stream ID の varint (最大 2^62-1) から `session_id = quarter_stream_id * 4` を復元し、正当なセッション ID (CONNECT ストリーム ID。クライアント起動の双方向ストリームであり %4==0、draft-ietf-webtrans-http3-16 Section 4) からは quarter_stream_id ≤ 2^60-1 のため int64 のラップで負にならない。ただし仕様逸脱ピアが 2^61 以上の巨大 varint を送った場合は負の `session_id` になり、フォールバックが生存セッションの先頭要素を選んで、無関係なセッションのデータグラムとして誤配送し得る
- `src/bindings/webtransport_h3.cpp` の `send_stream_data` は、`stream_info_` に未登録のストリームへの送信時、`session_ids_` の先頭要素をセッション ID として使う (複数セッション時に誤ったセッションにデータが属し得る)
- 上記 2 箇所のフォールバックは 0009 の設計方針で「STREAM_RESET 経路とは別のフォールバックであり本 issue の修正対象と独立しているため対象外とする (必要になったら別 issue とする)」と線引きされ、本 issue がその受託

## 設計方針

- DATAGRAM 分岐 (`src/webtransport/h3/server.py`): フォールバックを削除し、`receive_datagram` が復元した `session_id` をそのまま `on_datagram` に渡す (負の値は無効なセッション ID としてアプリが扱う。`on_datagram` の docstring に負値の可能性を追記する)。不正なセッション ID のデータグラムに対する draft-ietf-webtrans-http3-16 Section 4 の MUST (H3_ID_ERROR での接続クローズ) は本 issue の対象外とする (受信検証の拡張は別作業)
- `send_stream_data` (`src/bindings/webtransport_h3.cpp`): 処理は次の 3 ケースに整理する。(a) `stream_info_` に未登録のストリームへの送信: セッション ID を復元できないため、登録・バッファ追加・`nghttp3_conn_resume_stream` のすべてを行わず送信を無視する。(b) `stream_info_` に登録済みかつ書き込み登録済み (`is_write_registered == true`) のストリーム (自側 `open_stream` したストリーム): 従来どおり無条件にバッファ追加と `nghttp3_conn_resume_stream` を行う。(c) `stream_info_` に登録済みかつ書き込み未登録 (`is_write_registered == false`) のストリーム (受信済みのリモート起動ストリーム等): エントリのセッション ID で登録を試み、`nghttp3_conn_open_wt_data_stream` が成功した場合のみバッファ追加と `nghttp3_conn_resume_stream` を行う
- 高レベル API への影響: `Server.send_stream_data` 等から未登録ストリームへ送信しても黙って無視されるため、docstring に「未登録ストリームへの送信は無視される」旨を追記する (挙動の変更であり、利用者の誤用がサイレントになることを明示する)
- 0026 との関係: 死んだセッションのストリームへの事後 `send_stream_data` は、現状でも `close_session` が `erase_session_streams` で `stream_info_` を清掃するため到達可能であり、本 issue 単体で検証できる。0026 の stream_info_ 清掃 (CONNECT リセット経路の後始末) はこの経路をさらに広げるが、修正内容は同じ「無視」で対応できる (0026 側も実装順序を本 issue 先に推奨している)。誤配送されないことの検証を本 issue の完了条件に含める
- 既存テストへの影響: フォールバックに依存する既存テストは存在しない (`tests/test_webtransport_h3_ack_offset.py` 等の `send_stream_data` は `open_stream` 登録後の送信のみ。`tests/prop_webtransport_h3.py` の `prop_send_stream_data_arbitrary` はセッション未確立 (session_ids_ が空) の構成でクラッシュしないことのみを検証し、影響を受けない)
- 変更対象は `src/webtransport/h3/server.py` (DATAGRAM 分岐のフォールバック削除、`on_datagram` の docstring に負値の可能性を追記、`send_stream_data` の docstring に未登録ストリームへの送信は無視される旨を追記)、`src/bindings/webtransport_h3.cpp` (send_stream_data のフォールバック削除)、`src/webtransport/h3/client.py` (送信 API の docstring に未登録ストリームへの送信は無視される旨を追記)、テスト

## 完了条件

- フォールバック 2 箇所がセッション ID 集合の先頭要素に依存しなくなる
- `send_stream_data`: モックなしのテストで、複数セッションを確立した構成で未登録ストリームへの送信がどのセッションにも配送されないことを確認する (h3.Session 同士の直接受け渡し構成で、送信後に受信側のイベントにデータが現れないことと、送信側の `stream_buffers_` にエントリが残らないこと (`_has_stream_buffer` が None) を確認する。未登録ストリームには未使用のクライアント起動双方向ストリーム ID (%4==0) を使う。旧実装では先頭要素の生存セッションに誤配送され、修正後は無視されるため判別できる)
- 死んだセッションのストリームへの事後送信が誤ったセッションに配送されないこと (複数セッションを確立し、1 つのセッションで `open_stream` したストリームに対して `close_session` でセッションを終了した後に、そのストリームへ `send_stream_data` してもどのセッションにも配送されないことを、h3.Session 同士の直接受け渡し構成で確認する。バッファ残留がないことの確認も含める。close_session は `erase_session_streams` で `stream_info_` を既に清掃するため、0026 の実装を待たずに本 issue 単体で検証できる。実装時に、このテストが修正前の実装で落ちることを確認する (nghttp3 のストリーム終了状態によっては旧実装でも配送されない可能性があるため、判別力を確認する))
- DATAGRAM: 正常経路の動作は修正の影響を受けない。フォールバック経路は正常トラフィックで到達不能のため、コード検査でフォールバックが存在しないことを確認する

## 解決方法

`send_stream_data` と DATAGRAM 分岐のフォールバック 2 箇所を削除し、セッション ID 集合 (`session_ids_`) の先頭要素に一切依存しないようにした。

- `src/bindings/webtransport_h3.cpp` の `H3Session::send_stream_data` を 3 ケースに整理した。(a) `stream_info_` に未登録のストリームへの送信はセッション ID を復元できないため、登録・バッファ追加・`nghttp3_conn_resume_stream` のすべてを行わず黙って無視する。(b) 書き込み登録済み (`is_write_registered == true`) のストリームは従来どおりバッファ追加と resume を行う。(c) 書き込み未登録のストリーム (受信済みのリモート起動ストリーム等) はエントリのセッション ID で登録を試み、`nghttp3_conn_open_wt_data_stream` が成功した場合のみバッファ追加と resume を行う。旧実装の「登録失敗時にもバッファに残す」挙動も同時に解消した
- `src/webtransport/h3/server.py` の `_process_webtransport_events` の DATAGRAM 分岐から `get_session_ids()[0]` へのフォールバックを削除し、`receive_datagram` が復元した `session_id` をそのまま `on_datagram` に渡すようにした (負の値は無効なセッション ID としてアプリが扱う。`on_datagram` の docstring に負値の可能性と仕様節番号を追記)。`Server.send_stream_data` の docstring に未登録ストリームへの送信は無視される旨を追記した
- `src/webtransport/h3/client.py` の `send_stream_data` の docstring に未登録ストリームへの送信は無視される旨を追記した。`src/bindings/webtransport_h3.h` の `send_stream_data` の docstring も同様に更新した

テストは `tests/test_webtransport_h3_stream_buffer_cleanup.py` と `tests/test_e2e_webtransport_h3.py` に追加した。h3.Session 同士の直接受け渡し構成 (モックなし) で検証する。

- `test_send_to_unregistered_stream_is_ignored`: 複数セッション確立後に未登録ストリームへ送信しても受信側にデータが現れず、送信側の送信バッファにエントリが残らないことを確認。旧実装では先頭要素の生存セッションに誤配送されていた
- `test_send_to_closed_session_stream_is_ignored`: `close_session` で終了したセッションのストリームへの事後送信が無視されることを確認。旧実装では `nghttp3_conn_open_wt_data_stream` がプロセスを abort させていた
- `test_datagram_negative_session_id_passed_through`: 2^61 以上の Quarter Stream ID (8 バイト varint) を持つデータグラムで負のセッション ID がそのまま `on_datagram` に渡ることを e2e 構成で確認。旧実装ではセッション ID 集合の先頭要素にフォールバックして誤ったセッション ID を渡していた

なお、新テスト 3 本はいずれも修正前の実装で落ちることを実行確認済み (判別力あり)。
