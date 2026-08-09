# CONNECTION_CLOSE が UDP ロス時に再送されない

- Created: 2026-08-06
- Completed: 2026-08-09
- Branch: feature/fix-connection-close-retransmission
- Polished: 2026-08-08

## 目的

close() が生成した CONNECTION_CLOSE パケットが UDP ロスでピアに届かなかった場合、ピアは接続終了を検知できない問題を修正する。closing 状態のエンドポイントは受信パケットに応答して CONNECTION_CLOSE を送る (RFC 9000 Section 10.2.1) が、現状は一度しか送出されない。

本 issue は closed issue 0030 が「受信パケットへの CONNECTION_CLOSE 応答の再送は対象外とする」と線引きした判断を再検討するものである。0030 の根拠は「MUST / SHOULD ではない」ことであったが、実害として、CONNECTION_CLOSE がロスしたピアは、最後に受信したパケットからアイドルタイムアウト (既定 30 秒) が経過するまで接続終了を検知できない (ピア自身の送信継続はピア側のアイドルタイマーをリセットしない。RFC 9000 Section 10.1 は ack-eliciting 送信によるリセットを「最後の受信処理以降に他に ack-eliciting を送っていない場合」の 1 回に限定する)。リアルタイム通信では 30 秒の検知遅延は許容できない。また RFC 9000 Section 10.2.1 は受信応答の記述に加えて「endpoints MAY send the exact same packet in response to any received packet」と同一パケットの再送を明示的に許容している。

## 現状

- `src/bindings/quic.cpp` の `receive` メソッドは `closed_` が立った後の受信パケットをすべて破棄する (`if (!conn_ || closed_) { return 0; }`)
- `send` メソッドは `pending_close_packet_` を 1 回だけ返し、以降は `nullopt` を返す (0030 の完了条件として確定した契約)
- そのため、最初の CONNECTION_CLOSE が UDP ロスすると、ピアは最後に受信したパケットからアイドルタイムアウト (既定 30 秒) が経過するまで接続終了を検知できない
- RFC 9000 Section 10.2.1 は「An endpoint in the closing state sends a packet containing a CONNECTION_CLOSE frame in response to any incoming packet that it attributes to the connection」と記述する。大文字の MUST / SHOULD キーワードは無いが、BCP 14 のキーワードが無いことは規範性の否定にはならない (RFC 8174 Section 2)。あわせて「An endpoint SHOULD limit the rate at which it generates packets in the closing state」とレート制限を推奨する

## 設計方針

- close() で CONNECTION_CLOSE を生成できた場合 (保持パケットが存在する場合) に限り、close() 後の `receive()` は `closed_` ガードを外して `ngtcp2_conn_read_pkt` を呼び、その戻り値 (`NGTCP2_ERR_CLOSING`) で受信を検知する (ガードを外すのはこの場合のみ。生成できなかった場合、および受信経路で終了した接続では従来どおり `closed_` ガードを維持し、受信処理もイベント push も行わない)。ngtcp2 は CLOSING 状態で受信したパケットに `NGTCP2_ERR_CLOSING` を返す (サーバーではパケットの解析・DCID 照合は行われない。クライアントではパス照合が先行し、unknown path からのパケットは 0 が返って再アームされない)。パケットの帰属はアプリ層のアドレスベースのルーティングに委ねる (サーバーはクライアントアドレスで接続を振り分けるため、closing 状態の接続に渡されるパケットは同一ピア由来)
- `NGTCP2_ERR_CLOSING` を受けた場合、保持している CONNECTION_CLOSE パケットを再アームして `send()` が返せるようにする。ngtcp2 は CLOSING / DRAINING 状態では `ngtcp2_conn_write_connection_close` がパケットを生成しないため、再送は close() 時に生成したパケットの再利用以外に手段が無い
- 再送は受信データグラムごとに 1 回 (1:1) とし、間隔制限 (タイマー) は導入しない (receive() は 1 データグラムを処理するため、実質はデータグラム 1 個につき 1 回の再送になる。送出レートは受信レートに自然に制限される)。応答は常に close() 時に生成した同一パケットのみで、累積応答サイズは累積受信サイズを超えない。RFC 9000 Section 10.2.1 の増幅攻撃の 3 倍ルール (MUST) は鍵破棄エンドポイントと未検証アドレスへの送出に限定されるが、本実装は鍵を保持しつつ DCID 照合を行わないため、実質的に 1:1 応答で増幅にならないことを確認する
- 初回配送の契約 (0030) は維持する: `send()` は初回のみパケットを返し、receive() による再アームが無ければ以降は `nullopt` を返す (初回配送後にパケットを破棄せず保持し、receive() の受信応答時のみ再アームされる。保持と消費を分離する。保持するのは close() 時に生成したパケットのコピーであり、初回配送で消費されても残る)
- DRAINING 状態 (ピアの CONNECTION_CLOSE を受信済み) では再送しない (RFC 9000 Section 10.2.2 の「an endpoint in the draining state MUST NOT send any packets」)。ただし ngtcp2 は CLOSING 状態でパケットを処理しないため、ピアの CONNECTION_CLOSE を受信しても DRAINING には遷移しない (このガードは防御的)
- receive() の `NGTCP2_ERR_CLOSING` 分岐は、close() 起因の closing (保持パケットが存在する状態) では ConnectionClosed イベントを push しない (イベントの重複を防ぐ。アプリが自ら close() を呼んだため終了イベントは不要。close() 自体は ConnectionClosed イベントを push しない)。保持パケットが無い場合に `NGTCP2_ERR_CLOSING` が返るのは実質到達しない防御コードであり、その場合のみ既存どおり closed_ を立ててイベントを push する
- 高レベル API (src/webtransport/quic/client.py の close() / src/webtransport/quic/server.py の stop()) は close() 後に受信ループを回さないため、本修正の効果は低レベル API (Sans-IO) に限られる
- 既存テストの更新: `test_send_after_close` は receive() を挟まないため「2 回目以降は None」の断言は維持される。`test_receive_after_close` の断言 (result == 0) は修正後も成立する (保持パケットありのため受信処理が走り NGTCP2_ERR_CLOSING で 0 が返る)。コメントの更新と、再アームの検証 (close() 後の受信で send() が CONNECTION_CLOSE を返すことの追加) を行う。`test_conn_state_after_retry` は RETRY 経路で終了した接続 (保持パケットが無くガード維持条件に該当) のため修正後もそのまま通る (受信処理もイベント push も発生しない)
- 0029 / 0030 も同じ `src/bindings/quic.cpp` の `closed_` 周辺を変更対象としており (両者とも完了済み)、既存の変更との整合に注意する
- 変更対象は `src/bindings/quic.cpp` (receive / send / close)、`src/bindings/quic.h` (send() のドキュメントコメント「1 回だけ返し、以降は nullopt」の更新と再送用の状態保持)、テスト (tests/test_quic_error_handling.py / tests/test_quic_conn_state.py)、CHANGES.md (## develop セクションへの [FIX] エントリ)

## 完了条件

- close() 後に送信した CONNECTION_CLOSE がピアに届かない (UDP ロス) 状態で、ピア (まだ接続が生きていると思っている) がパケットを送ってきた場合、`receive()` → `send()` が CONNECTION_CLOSE を再送する
- 再送は受信データグラムごとに 1 回で、受信が無ければ再送されない。receive() を挟まない 2 回目の `send()` は従来どおり None を返す (0030 の契約維持)
- モックなしの Sans-IO テストで確認する (UDP ロスは「CONNECTION_CLOSE パケットをピアに渡さない」ことで再現する。close() → send() で CONNECTION_CLOSE を生成して保持 → パケットをピアに渡さず、ピア (クライアント) が `send_stream_data` で送信データを積んで `send()` で生成したパケットをサーバーの receive() に渡す → send() が CONNECTION_CLOSE を再送することを検証する)

## 解決方法

- `src/bindings/quic.h` に再送用の状態保持 (`pending_close_packet_` の保持継続と `close_packet_armed_` フラグ) を追加し、`send()` のドキュメントを「初回は必ず返し、receive() が再アームするまで nullopt」に更新した
- `src/bindings/quic.cpp` の `receive` メソッドは、close() で CONNECTION_CLOSE を生成できた場合 (保持パケットが存在する場合) のみ `closed_` ガードを外して `ngtcp2_conn_read_pkt` を呼び、`NGTCP2_ERR_CLOSING` で受信を検知して再アームするようにした。DRAINING 状態では再送しない防御ガードも入れた
- `src/bindings/quic.cpp` の `send` メソッドは、`close_packet_armed_` が true のときだけ保持パケットのコピーを返し、返した後は受信応答で再アームされるまで nullopt を返すようにした (0030 の初回配送契約を維持)
- 再送は受信データグラムごとに 1 回 (1:1) で、同一パケットを再送する。増幅攻撃の 3 倍ルール・レート制限・パケット帰属の前提を `receive` のコメントに明記した
- `tests/test_quic_error_handling.py` に `test_connection_close_retransmission_on_receive` を追加し、`test_receive_after_close` を self.close() 後の再アーム検証に更新した。`tests/test_quic_conn_state.py` に `test_close_mid_handshake_retransmits_connection_close` を追加し、`test_close_before_handshake` に保持パケット無し経路の receive() 検証を追記した
- `CHANGES.md` の `## develop` セクションに `[FIX]` エントリを追加した
