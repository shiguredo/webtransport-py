# WebTransport データストリームのリセットで RESET_STREAM_AT を送出する

- Created: 2026-08-04
- Completed: {YYYY-MM-DD}
- Branch: feature/add-reset-stream-at
- Polished: 2026-08-07

## 目的

draft-ietf-webtrans-http3-16 Section 4.4 の MUST「WebTransport implementations MUST use the RESET_STREAM_AT frame with a Reliable Size set to at least the size of the WebTransport header when resetting a WebTransport data stream」を満たし、WT ヘッダー (ストリームタイプ + セッション ID) の確実な配信を保証する。現状は通常の RESET_STREAM しか送出しないため、MUST に違反している。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::reset_stream` は `ngtcp2_conn_shutdown_stream_write(conn_, 0, stream_id, error_code)` を呼ぶ (`NGTCP2_SHUT_STREAM_FLAG_NONE`。通常の RESET_STREAM 送出)
- ngtcp2 (webtransport ブランチ) は `NGTCP2_SHUT_STREAM_FLAG_FLUSH` フラグで RESET_STREAM_AT を送出できる (in-flight のストリームデータが ACK されるまでストリームを閉じない。reliable stream reset (draft-ietf-quic-reliable-stream-reset-09) Section 5.3)。このフラグは、ピアが `reset_stream_at` transport parameter (受信対応の広告) で対応を広告していない場合は無視される (ngtcp2 の実装挙動)
- `reset_stream_at` transport parameter (`ngtcp2_transport_params` の `reset_stream_at` フィールド) の設定が `src/bindings/quic.cpp` に無い
- ストリームのセッション関連付けはストリーム先頭の WT ヘッダー (draft-ietf-webtrans-http3-16 Section 4.2 / 4.3。ストリームタイプ / Signal Value + セッション ID) のみで行われ、ヘッダーが失われるとセッション ID を復元できない。このため h3 層の `H3Session::close_stream` のセッション ID 復元が -1 にフォールバックするケースが残る (0009)

## 設計方針

- `QuicConnection::reset_stream` で `NGTCP2_SHUT_STREAM_FLAG_FLUSH` を渡す。ngtcp2 はピアが `reset_stream_at` を広告し、かつリセット時点で書き込み済みデータがある (tx offset > 0) 場合に RESET_STREAM_AT を送出し、データ未書き込みのストリームは従来どおり通常の RESET_STREAM を送出する (データ未書き込みのリセットは MUST の対象外。Reliable Size 0 の RESET_STREAM_AT は RESET_STREAM と等価であり、データを配信する意図なしでリセットする場合はどちらを使ってもよい (draft-ietf-quic-reliable-stream-reset-09 Section 5))
- `reset_stream_at` transport parameter を設定する (受信対応の広告。draft-ietf-quic-reliable-stream-reset-09 Section 3 の「Support for receiving RESET_STREAM_AT frames is advertised by sending the reset_stream_at (0x1d) transport parameter」。draft は空の値での広告を要求し、非空の値は TRANSPORT_PARAMETER_ERROR (MUST)。ngtcp2 は内部フィールドの 1 を空値として符号化する)。設定はクライアント / サーバーの両方で行う (両側必須: 送出側はピアの広告を参照して RESET_STREAM_AT を送出し、受信側は自身が広告していないと RESET_STREAM_AT を受信した時点で NGTCP2_ERR_FRAME_ENCODING により接続を閉じる (ngtcp2 の実装挙動)。draft-ietf-webtrans-http3-16 Section 3.1 もクライアント・サーバー両方での広告を要求する)
- 相互運用の制約: 現行の _deps キャッシュの ngtcp2 (webtransport ブランチ) はこの transport parameter を旧ドラフトの ID (0x17F7586D2CB571) で送出する (draft-09 の 0x1d ではない。RESET_STREAM_AT フレームタイプは 0x24 で一致)。このため MUST の達成は本ライブラリ同士の接続に限定される (draft-09 準拠の第三者実装はお互いの広告を認識しない)。deps.json はブランチ固定のため、キャッシュ更新で upstream の webtransport ブランチ (0x1d + 旧 ID 両対応) に切り替わった場合は解消される。実装時はビルドされる ngtcp2 の TP ID を確認する
- 設定箇所は 4 箇所: `initialize_client` / `initialize_server` / `initialize_server_from_packet` の transport params と、`setup_server_early_data` の 0-RTT early data コンテキスト。0-RTT 利用時は両エンドポイントがこの transport parameter の値を記憶する必要があり (draft-ietf-quic-reliable-stream-reset-09 Section 3 の「both endpoints MUST remember the value of this transport parameter」)、サーバーが 0-RTT を受け入れる場合は再開コネクションでこの拡張を無効化してはならない (同 Section 3 の「When the server accepts 0-RTT data, the server MUST NOT disable this extension on the resumed connection」) ため、early data コンテキストにも含めて恒常的に広告する
- Reliable Size は ngtcp2 がリセット時点の書き込み済みオフセット (tx offset) 全体に設定する。QUIC 層 (ngtcp2) に書き込み済みのデータはすべて保証されるため、WT ヘッダーが先頭に書かれていれば「Reliable Size ≥ WT ヘッダーサイズ」は構造的に保証される。Reliable Size は Final Size 以下である必要がある (draft-ietf-quic-reliable-stream-reset-09 Section 4 の MUST) が、ngtcp2 は両方を同じオフセットに設定するため抵触しない。なお、h3 層・QUIC 層の送信バッファに留まっているデータ (ngtcp2 に未書き込み) は保証の対象外 (リセット時に破棄される) ため、リセット前に送信処理 (send()) を済ませる必要がある
- 変更対象は `src/bindings/quic.cpp` (reset_stream のフラグ変更・transport parameter の設定) とテスト (`tests/test_e2e_webtransport_h3.py`。0009 の低レベル API クライアント構成を流用)。`src/bindings/quic.h` は reset_stream のシグネチャが不変のため変更不要の見込み。h3 層は変更不要 (全 WT リセット経路が `QuicConnection::reset_stream` に集約されている。受信側も RESET_STREAM_AT は通常の STREAM_DATA → STREAM_RESET と同じイベント列になる。`H3Session::close_stream` の同期コールバック (reset_stream_cb) 経由で `quic_connection.reset_stream` が二重に呼ばれる経路があるが、ngtcp2 はリセット済みのストリームには即 return するため FLUSH 化後も挙動は変わらない)
- 影響範囲: `QuicConnection::reset_stream` は http3 層 (HTTP/3 のリクエストストリーム) からも呼ばれるため、本ライブラリ同士の接続では HTTP/3 のリセットも RESET_STREAM_AT になる (プロトコル上は合法)。`reset_stream_at` を広告しないピアには従来どおり通常の RESET_STREAM が送出されるため後方互換は保たれる
- 対象外: STOP_SENDING 受信に応答して ngtcp2 が内部で送るリセット (NONE フラグのまま。draft-ietf-quic-reliable-stream-reset-09 Section 5.4 の SHOULD に整合)、データ未書き込みストリームのリセット、`QuicConnection::close_stream` (shutdown_stream。NONE フラグのまま変更しない。h3 / http3 層からは呼ばれていないが公開 API として残る。WT データストリームのリセットは `reset_stream` 経由のみ)
- 0016 (ストリーム・接続制御 API) / 0031 (CONNECTION_CLOSE の再送) も同じ `src/bindings/quic.cpp` を変更対象とし、0026 (CONNECT ストリームのセッション後始末) は同じ `tests/test_e2e_webtransport_h3.py` の低レベル API クライアント構成を変更対象とするため、実装順序によるマージの競合に注意する

## 完了条件

- QUIC 層 (ngtcp2) に書き込み済みデータがあるデータストリームのリセットで RESET_STREAM_AT が送出され、ピア側のセッション ID 復元が失敗しなくなる (該当ケースが -1 にフォールバックしなくなる)。データがリセットより先に届く通常の順序では RESET_STREAM_AT の有無にかかわらず復元できるため、テストはデータパケットを保留してリセット送出パケットを先に届ける構成で検証する。FLUSH 時、ngtcp2 は未 ACK のストリームデータを保持し、リセット送出パケットに同梱して再書き出しする。ピアは RESET_STREAM_AT 受信時に reliable size 分のデータ到着までリセットを確定しない (draft-ietf-quic-reliable-stream-reset-09 Section 5.3 の Size Known → Data Recvd 遷移) ため、同パケット内の WT ヘッダーが配信されてからリセットが確定し、セッション ID が復元される (本ライブラリ同士 (両側が広告) の接続での挙動)
- データ未書き込みストリームのリセットは従来どおり通常の RESET_STREAM のままで、0009 の「データ未受信のままリセット → -1」テスト (`test_stream_reset_before_data_received_minus_one`) は変更せず引き続き通る (テストのロジックは変更しないが、docstring の理由付け (RESET_STREAM_AT 未対応 → データ未書き込み) は実装時に更新する)
- モックなしのテストで検証できる (0009 の低レベル API クライアント構成に、送信済みデータのパケットを生成後にソケットへ送らず保持する機構を追加し、データがリセットより先に届かない順序 (リセット送出パケットを先に送信) で確認する。修正後はリセットパケットにデータが同梱されるため、保持パケットの解放は検証の成立に影響しない。データは 1 パケットに収まる小さなサイズにする (send() は 1 回で 1 パケットしか返さず、複数パケットに分かれると未書き込み分がリセット時に破棄されて意図と乖離する))
