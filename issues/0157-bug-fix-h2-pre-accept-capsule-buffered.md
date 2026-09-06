# WebTransport over HTTP/2 のサーバーが受理前に届いた楽観的カプセルを破棄する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-pre-accept-capsule-buffered
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/2 のサーバーは `on_data_chunk_recv_callback` で `is_established` が false ならペイロードを捨てる。サーバー側の `accept_session` は Python 高レベル (`h2/server.py`) が `SESSION_READY` イベント取得後に呼ぶため、同一 TCP 読み取り内で HEADERS (CONNECT) と DATA (楽観的カプセル) が連続して届くと DATA は破棄される。draft-ietf-webtrans-http2-15 Section 3.2 は「A client MAY optimistically send any WebTransport capsules ... without waiting for a response」を許容し、サーバーには「MUST NOT process any capsules on the request stream unless it accepts the WebTransport session」(受理前に処理しない = 受理後に処理する) を求めており、破棄は仕様違反。h3 側は nghttp3 がバッファするため同じ問題は起きない。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_data_chunk_recv_callback` は `wt_session && wt_session->is_established` を条件に `process_capsules` を呼ぶ (未確立なら黙って捨てる)
- サーバーの `accept_session` は Python 高レベル (`src/webtransport/h2/server.py` の `Server._handle_client`) が SESSION_READY イベント取得後に呼ぶ
- 実験: Sans-IO でクライアントが connect 直後に `send_datagram` を呼び、サーバーが HEADERS + DATAGRAM を同一 / 別の `receive` で読んだ後に `accept_session` を呼んでも `Datagram` イベントは発火しない
- `tests/test_webtransport_h2_datagram.py` の `test_send_datagram_client_optimistic_delivered` はクライアントがワイヤに出すことしか確認しておらず、サーバーが受理後に配信するかは検証していない

## 設計方針

- 受理前は `capsule_buffer` に上限付きで蓄積する (draft-15 Section 3.2 の「Bytes received before the server sends the response are processed once the session is accepted or discarded if the session is rejected」)
- `accept_session` で受理した瞬間に蓄積したカプセルを `process_capsules` で処理し、`SessionReady` の後に `Datagram` / `StreamData` イベントを発火する
- `reject_session` で拒否した場合はバッファを破棄する
- 蓄積の上限は Config に追加するか、既存の `max_frame_size` 相当の値に固定する
- 別 issue の `capsule_buffer` 無制限蓄積の DoS 対策 (issue 0158) と整合するよう、上限値を統一する

## 完了条件

- クライアントの楽観的送信 (connect 直後の `send_datagram` / `send_stream_data`) が、サーバーの `accept_session` 後に配信されること
- `reject_session` の場合は蓄積が破棄されること
- 蓄積の上限を超えた場合は WT_ERROR で拒否すること
- `tests/test_webtransport_h2_datagram.py` に、受理前カプセルの配送を検証するテストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
