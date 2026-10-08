# h3 で DATAGRAM がまれに受信側へ配送されない

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-datagram-delivery-loss
- Polished: 2026-10-08

## 目的

WebTransport over HTTP/3 で、送信側が送出まで進んだ DATAGRAM が受信側の `on_datagram` に届かないことがまれに起きる問題を調査する。ループバックの小さなデータグラムであり再送も分割もないため、QUIC 層のどこかでデータグラムが黙って破棄されるか、送出契機が失われている。

報告環境は webtransport-py 2026.1.0.dev18 であり、これは既に修正済みの 0209 (macOS の kqueue セレクタで受信パケットを取りこぼす) の修正前にあたる。まず現行版で再現するかを確認し、再現する場合に切り分けて修正する。

## 現状

### 報告 (moqt-py の E2E テスト。外部観測であり本リポジトリでは未検証)

- webtransport-py 2026.1.0.dev18、CPython 3.14、macOS arm64 (GitHub Actions の macos-26 ランナーでも再現)
- クライアントとサーバーを同一プロセス内の asyncio で動かし、127.0.0.1 の UDP で通信する
- moqt-py の E2E テストで再現する。サーバーの publication から小さなオブジェクトデータグラム (報告では 12〜17 バイト。MOQT のオブジェクトメッセージ長) を 1 つ送り、クライアント側で 5 秒待って受信を確認する (`uv run pytest -q tests/test_e2e.py -k datagram`)。失敗率は「数回に 1 回程度」、CPU 負荷で再現しやすい、該当テストを単独で 30 回実行した場合は再現しない、という報告である (失敗率は定性的な記述で、分母と失敗数の実測記録は報告に無い)
- 失敗は h3 経路のみという報告である (h2 では観測されていない)
- 一時ログによる観測: (1) サーバーのアプリが `h3.Server.send_datagram` を呼ぶ、(2) `Server._send_to` が `H3Session.get_datagrams_to_send()` の 1 件を `quic_connection.send_datagram()` へ渡し、`quic_connection.send()` が返した 1 パケットを `loop.sock_sendto` で送出する、(3) クライアント側の socket がほぼ同時刻に 30 / 30 / 32 バイトの 3 パケットを受信する、(4) `Client._process_quic_events` で `quic.EventType.DATAGRAM` が観測されず `on_datagram` が呼ばれない、(5) 5 秒待ってタイムアウトする

### 報告環境に 0209 の修正が入っていないこと (本 issue 作成時に確認)

- `VERSION` の dev18 bump は `9b77d03` (2026-09-14 22:47:48)、0209 の修正コミット `0a5d1e8` (macOS の受信パケット取りこぼしを `src/webtransport/_common.py` の `recv_datagram` へ置き換えて修正) は 2026-09-15 09:02:31 である。dev18 は 0209 の修正前であり、報告された症状 (macOS arm64・まれ・CPU 負荷で再現しやすい・`DATAGRAM` イベントが来ない) は 0209 の機序と区別できない
- moqt-py の `uv.lock` は `webtransport-py 2026.1.0.dev18` を固定している。本リポジトリの現行 `VERSION` は 2026.1.0.dev21
- したがって、最初に現行版での再現を確認する必要がある。再現しない場合は、本 issue が報告する症状は 0209 の機序であり現行版では修正済みということになる

### 観測 3 のパケット長の解釈 (再計算)

- 短ヘッダパケットのオーバーヘッドは 1 (先頭バイト) + 8 (DCID。`QuicConnection` は `scid.datalen = NGTCP2_MIN_INITIAL_DCIDLEN` で 8 バイト固定) + 1〜4 (パケット番号) + 16 (AEAD タグ) = 26〜29 バイト、DATAGRAM フレームは type 1 バイト + 長さ varint 1〜2 バイト、WebTransport データグラムは先頭に Quarter Stream ID の varint (セッション ID が小さい間は 1 バイト) を含む
- アプリケーションデータが 12〜17 バイトなら、DATAGRAM を含むパケットは 41 バイト以上になる。観測された 30 / 30 / 32 バイトでは運べない
- 一方、アプリケーションデータが 0 バイトなら DATAGRAM を含むパケットは 29〜32 バイトになり、観測値と一致し得る。ACK のみのパケットも同程度の長さになる
- したがってパケット長だけでは「DATAGRAM を含むパケットが受信側 socket に届いたか」を判別できない。観測 3 の 3 パケットは DATAGRAM を含む可能性があり、含む場合でも届いたパケットが ACK だけの可能性もある。区間内の全受信を記録したのか同時刻の 3 件だけを記録したのかも報告からは判別できない
- このため切り分けは、パケット長の解釈ではなく設計方針 2 の (a) の実測 (受信したパケット数と `quic.ReceiveResult` の内訳、DATAGRAM フレームの有無) から始める

### コードで確認できている事実

- 送信経路は 2 段キューである。`H3Session::send_datagram` が `pending_datagrams_` に積み、`H3Session::get_datagrams_to_send` が取り出してキューをクリアし、`QuicConnection::_send_datagram_unchecked` → `QuicConnection::send_datagram` が `datagram_queue_` に積み、`QuicConnection::send` が `ngtcp2_conn_writev_datagram` でパケット化する。h3 層のキューは QUIC 層が受理する前にクリアされるため、QUIC 層で破棄されても h3 層からは送出済みに見える
- `QuicConnection::send_datagram` は、接続が無い・接続が閉じている・`config_.enable_datagram` が偽の場合と、`remote_max_datagram_frame_size` が既知でフレーム長が上限を超える場合に黙って破棄する
- `QuicConnection::send` のデータグラムループは `NGTCP2_ERR_INVALID_ARGUMENT` (フレーム長がピアの上限超過) と `NGTCP2_ERR_INVALID_STATE` (ピアの `max_datagram_frame_size` = 0 の恒久状態) で `datagram_queue_` の先頭を破棄する。ピアの transport parameter 未受信と `nwrite == 0` は保留して次回の呼び出しに回す
- フレーム長の見積り `1 + quic_varint_length(data.size()) + data.size()` は ngtcp2 の `ngtcp2_pkt_datagram_framelen` と同値で、varint 境界 (63 / 16383 / 1073741823) も一致する。上限判定は RFC 9221 Section 3 の「larger than」とも整合しており、見直しの対象ではない
- ngtcp2 の契約では `NGTCP2_WRITE_DATAGRAM_FLAG_MORE` 付きの `ngtcp2_conn_writev_datagram` は「パケットを書いたが DATAGRAM は入れなかった」場合に `nwrite > 0` かつ `*paccepted == 0` を返し得る。この場合 `send()` は DATAGRAM を含まないパケットを返し、データグラムは `datagram_queue_` に残る。`nwrite == 0` のときは `*paccepted == 0` が保証される
- `send()` は 1 回の呼び出しで 1 パケットだけ返す。`Server._send_to` は `send()` が None を返すまで drain し、`Server.run` は受信もタイマーも無い周でも `_send_to` を呼ぶ (待機は `Server._timeout_seconds` で 0.001〜0.1 秒)。したがって `datagram_queue_` に残ったデータグラムは通常 0.1 秒以内に再試行されるが、`send()` が `nwrite == 0` を返し続ける間は送出されない
- 1 パケットで送れるデータグラムの上限は経路 MTU に依存する。ngtcp2 の `ngtcp2_settings_default` は `max_tx_udp_payload_size = 1500 - 48 = 1452` を設定し、`QuicConnection` はこれを上書きしない。DATAGRAM は分割できないため、1 パケットに収まらないサイズは送出されない
- 受信経路は `Client._receive` がソケットで読んだ全パケットを `quic.Connection.receive` に渡し、`Client._process_quic_events` が `next_event()` を drain する。`Client._receive` にパケットを選別する処理は無く、`quic.Connection.receive` の戻り値 (`quic.ReceiveResult` の ACCEPTED / DISCARDED / CLOSED) は捨てている
- `H3Session::receive_datagram` は、Quarter Stream ID × 4 のセッションが `session_ids_` に無い場合と、受理前 FIN を検知済みのセッションである場合に黙って破棄する (draft-ietf-webtrans-http3-16 Section 4.6 の SHOULD バッファリングに対する意図的な逸脱としてコード内コメントに記録がある)
- 「ループバックなので輻輳は関係しない」とは断定できない。`send()` の `nwrite == 0` は輻輳制限・pacing・amplification limit で発生し、保留のまま再試行され続ける経路がある

## 設計方針

1. **最初に現行版で再現するかを確認する**。報告環境 (dev18) は 0209 の修正前であるため、現行版で再現しない場合は 0209 の機序を第一候補として扱う。再現確認はリポジトリ内のテストで行い、moqt-py の環境に依存しない
2. **切り分けは受信側の入口から層の順に行う**。観測点は次の順に置く
   - (a) 受信ソケット層: `Client._receive` が読んだパケット数と `quic.Connection.receive` の `quic.ReceiveResult` の内訳を、送信側の `loop.sock_sendto` のパケット数と突き合わせる (ループバックでは一致するはずで、不一致なら受信ソケット層が第一容疑)
   - (b) 送信側の送出パケットに DATAGRAM フレームが含まれたか (ngtcp2 の `*paccepted` と `nwrite`)
   - (c) 送信側の破棄経路 (`QuicConnection::send_datagram` の 3 条件と `QuicConnection::send` の `NGTCP2_ERR_INVALID_ARGUMENT` / `NGTCP2_ERR_INVALID_STATE`)
   - (d) 受信側 `quic.Connection.receive` が DATAGRAM フレームを処理したか
   - (e) `H3Session::receive_datagram` のセッション判定
3. **計測は一時的なものとする**。原因特定のためのログとカウンタは本 issue の成果物ではなく、完了前に削除する。恒久的な観測 API が必要と判断した場合は、別 issue として起票する
4. **原因ごとに修正方針を分ける**
   - 受信ソケット層 (0209 の機序の再発または別の取りこぼし) なら `src/webtransport/_common.py` の受信ヘルパーと `Client._receive` を直す
   - 送信側の破棄なら破棄理由を記録したうえで破棄条件かキュー処理を直す。フレーム長の見積りは ngtcp2 と一致しているため変更しない
   - 送信側の送出契機の欠落なら `Server.run` の送信タイミングか `QuicConnection::send` の保留処理を直す
   - 受信側の h3 層のセッション判定ならセッション ID の復元と登録タイミングを直す
   - ngtcp2 側で回避不能なら、再現条件と回避不能の根拠を「解決方法」に記録し、上流待ちとして pending にする。依存ライブラリはフォークしない (CODEBASE.md)
5. **対症療法を先に入れない**。リトライや重複送出を原因特定前に足さない (E2E テスト用途で事実と異なる挙動を観測させることになる)
6. **再現テストは負荷条件と反復回数を固定して常設する**。負荷の種類と量、反復回数、待ち時間、合否基準をテストに明記する。負荷の強い長時間反復は既定のスイートに含めず、環境変数またはマーカーによる明示実行にする

## 完了条件

1. `tests/` に再現テストが追加されている。内容は「h3 のセッションを確立し、小さなデータグラムの送受信を反復する。1 回ごとに 5 秒のタイムアウトで受信を確認し、CPU 負荷 (バックグラウンドのビジーループ。プロセス数と実行時間をテストに明記) をかけた状態で N 回反復する」。既定のスイートでは短い反復 (10 回程度) とし、長時間反復 (N ≥ 100) は環境変数で明示実行できるようにする。負荷条件と N はテストコードと「解決方法」に記録する
2. 現行版で再現しない場合は、0209 の修正 (コミット `0a5d1e8`、2026-09-15) が報告環境 (dev18、2026-09-14) より後である事実と 1 のテスト結果を根拠に、既修正であることを「解決方法」に記録して issue を closed にする。moqt-py の pin 更新は本リポジトリの範囲外である旨も記録する
3. 現行版で再現する場合は、設計方針 2 の順でどの層でデータグラムが消えるかを特定し、根拠 (観測結果とシンボル) を「解決方法」に記録して修正する。ngtcp2 側で回避不能と判明した場合は修正せず、再現条件と回避不能の根拠を「解決方法」に記録して pending にする (この場合、完了条件 4・5 は適用しない)
4. 修正した場合、1 のテストが修正前の実装で失敗し、修正後は同一の負荷条件と反復回数で失敗 0 件になる。確率的に再現する場合は、修正前の失敗率 p を実測して記録し、修正後の反復回数 n は `(1 - p)^n <= 0.05` を満たす値以上とする (修正後に残存する失敗を見逃す確率を 5 % 以下にする。p = 1/4 なら n = 11)
5. 修正した場合、データグラムのサイズは 1 パケットに収まる範囲で複数を対象にする。小さなサイズ (報告の 12〜17 バイト相当) と、`quic.Connection.path_max_tx_udp_payload_size` から QUIC / HTTP/3 のヘッダ分を引いた最大長の近傍。1500 バイト級は `ngtcp2_settings_default` の `max_tx_udp_payload_size` = 1452 により 1 パケットに収まらないため対象外とし、その理由を「解決方法」に記録する
6. 既存のデータグラム関連テスト (`tests/test_webtransport_h3_datagram.py`、`tests/test_e2e_webtransport_h3.py`) と全テストが通過する
7. モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
8. スループット系テスト (`tests/test_e2e_webtransport_h3_throughput.py`) が通過し、性能劣化が無い

## 解決方法
