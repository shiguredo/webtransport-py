# h3 で DATAGRAM がまれに受信側へ配送されない

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-datagram-delivery-loss

## 目的

WebTransport over HTTP/3 で、送信側が送出まで進んだ DATAGRAM が受信側の `on_datagram` に届かないことがまれに起きる問題を調査して修正する。ループバックの 1 パケットであり輻輳も再送も関係しない状況で再現するため、QUIC 層のどこかでデータグラムが黙って破棄されるか、送出契機が失われている。

## 現状

### 再現条件 (報告)

- webtransport-py 2026.1.0.dev18、CPython 3.14、macOS arm64 (GitHub Actions の macos-26 ランナーでも再現)
- クライアントとサーバーを同一プロセス内の asyncio で動かし、127.0.0.1 の UDP で通信する
- moqt-py の E2E テストで再現する。サーバーの publication から 12〜17 バイトのオブジェクトデータグラムを 1 つ送り、クライアントで 5 秒待つ (`uv run pytest -q tests/test_e2e.py -k datagram`)。5 秒のタイムアウトで失敗する確率は数回に 1 回程度で、CPU 負荷をかけると再現しやすい。該当テスト単独の 30 回実行では再現しない
- 失敗は h3 経路のみである (h2 では観測されていない)

### 観測 (報告者の一時ログ)

`src/webtransport/h3/server.py` の `Server._send_to` と `src/webtransport/h3/client.py` の `Client._process_quic_events` に一時ログを入れた計測で、失敗時は次の順で観測された。

1. サーバーのアプリが `h3.Server.send_datagram` を呼ぶ (`_clients` に該当クライアントがあり `webtransport_session` も None ではない)
2. `_send_to` で `H3Session.get_datagrams_to_send()` が 1 件返し、`quic_connection.send_datagram()` に渡したうえで、`quic_connection.send()` が返した 1 パケットを `loop.sock_sendto` で送出する
3. クライアント側の socket はほぼ同時刻に 30 / 30 / 32 バイトの 3 パケットを受信する
4. クライアント側の `Client._process_quic_events` で `quic.EventType.DATAGRAM` が観測されず `on_datagram` が呼ばれない
5. 5 秒待っても届かずタイムアウトする

成功時は同じ経路の全段が観測される。C++ 拡張の内部までは追えていない。

### コードで確認できている事実 (本 issue 作成時の調査)

- 送信経路は 2 段キューである。`H3Session::send_datagram` が `pending_datagrams_` に積み、`H3Session::get_datagrams_to_send` が取り出してキューをクリアし、`QuicConnection::_send_datagram_unchecked` → `QuicConnection::send_datagram` が `datagram_queue_` に積み、`QuicConnection::send` が `ngtcp2_conn_writev_datagram` でパケット化する。h3 層のキューは QUIC 層が受理する前にクリアされるため、QUIC 層で破棄されても h3 層からは「送出済み」に見える
- `QuicConnection::send_datagram` は `remote_max_datagram_frame_size` が既知でフレーム長が上限を超える場合、`config_.enable_datagram` が偽の場合、接続が閉じている場合に黙って破棄する
- `QuicConnection::send` のデータグラムループは `NGTCP2_ERR_INVALID_ARGUMENT` と `NGTCP2_ERR_INVALID_STATE` (ピアの transport parameter 既知、すなわち `max_datagram_frame_size` = 0 の恒久状態) で `datagram_queue_` の先頭を破棄する。ピアの transport parameter 未受信 (`NGTCP2_ERR_INVALID_STATE` で上限が不明) と `nwrite == 0` (輻輳制限・pacing・amplification limit) は保留して次回の呼び出しに回す
- ngtcp2 の契約では `NGTCP2_WRITE_DATAGRAM_FLAG_MORE` 付きの `ngtcp2_conn_writev_datagram` は「パケットを書いたが DATAGRAM は入れなかった」場合に `nwrite > 0` かつ `*paccepted == 0` を返し得る。この場合 `send()` は DATAGRAM を含まないパケットを返し、データグラムは `datagram_queue_` に残る。`nwrite == 0` のときは `*paccepted == 0` が保証される
- `send()` は 1 回の呼び出しで 1 パケットだけ返す。`Server._send_to` は `send()` が None を返すまで drain し、`Server.run` は受信もタイマーも無い周でも `_send_to` を呼ぶ (待機は最大 0.1 秒)。したがって `datagram_queue_` に残ったデータグラムは通常 0.1 秒以内に再試行される
- 受信経路は `Client._receive` がソケットで読んだ全パケットを `quic.Connection.receive` に渡し、`Client._process_quic_events` が `next_event()` を drain する。`Client._receive` にはパケットを選別する処理は無い
- 受信側 h3 層の `H3Session::receive_datagram` は、Quarter Stream ID × 4 のセッションが `session_ids_` に無い場合と、受理前 FIN を検知済みのセッションである場合に黙って破棄する (draft-ietf-webtrans-http3-16 Section 4.6 の SHOULD バッファリングに対する意図的な逸脱としてコード内コメントに記録がある)。セッションが確立済みの再現手順では該当しないと考えられるが、確認は必要である
- 0209 (closed) は macOS の kqueue で `asyncio.wait_for` に包んだ `loop.sock_recvfrom` が読み取り可能通知を失う問題で、`_common.py` の `recv_datagram` に置き換えて修正済みである。本件は「socket が受信している」観測があるため別経路の可能性が高いが、観測点がカーネル側 (パケットキャプチャ) か受信ループ内 (`recvfrom` 後) かで切り分けが変わる

## 設計方針

原因は未特定である。次の順で切り分けてから修正方針を決める。

1. **観測点を C++ 層まで広げる**。どの層で消えているかを 1 回の再現で断定できるようにする。候補は (a) `QuicConnection::send_datagram` の破棄経路、(b) `send()` が返したパケットに DATAGRAM フレームが含まれるか、(c) 受信側 `quic.Connection.receive` が DATAGRAM フレームを処理したか、(d) `H3Session::receive_datagram` のセッション判定。ログを一時的に入れるのではなく、E2E テスト向けライブラリの目的 (CODEBASE.md の「プロトコルレベルの制御と観測」) に沿って観測 API として公開することを第一候補とする (例: 送信側のデータグラム破棄理由のカウンタ、`send()` のパケットに含まれるフレーム種別の取得)
2. **リポジトリ内で再現するテストを追加する**。moqt-py に依存せず、h3 の E2E で小さなデータグラムを連続送出し、負荷下で反復して再現させる。単独 30 回で再現しないため、CPU 負荷 (ビジーループや並行タスク) と反復回数をパラメータにする
3. **切り分け結果に応じた修正**
   - 送信側で `datagram_queue_` に残ったまま送出契機が失われるなら、送出契機 (run ループの送信タイミング、タイマー期限の算出) を直す
   - 送信側の黙った破棄 (フレーム長上限・`INVALID_STATE`) が原因なら、破棄理由を観測できるようにしたうえで、上限判定の境界 (varint 長の見積り) を見直す
   - 受信側の h3 層のセッション判定が原因なら、セッション ID 復元と登録タイミングを直す
   - ngtcp2 側の破棄であれば、再現条件を issue に記録したうえで、呼び出し側で回避できるか (送出順・パケット組み立て) を検討する。依存ライブラリはフォークしない (CODEBASE.md)
4. **暫定の回避策を先に入れない**。原因が特定できるまで、リトライや重複送出のような対症療法は入れない (E2E テスト用途で事実と異なる挙動を観測させることになるため)

## 完了条件

- リポジトリ内に再現テストが追加され、修正前の実装で失敗する (負荷条件と反復回数をテストに明記する)
- どの層でデータグラムが消えていたかを特定し、「解決方法」に根拠 (観測結果と該当シンボル) を記録する
- 修正後、負荷をかけた反復実行 (少なくとも 100 回) で失敗しない
- 12〜17 バイトのデータグラムだけでなく、複数サイズ (小さめ・1500 バイト近傍・上限近傍) でも配送される
- 既存のデータグラム関連テスト (`tests/test_webtransport_h3_datagram.py`、`tests/test_e2e_webtransport_h3.py` 等) と全テストが通過する
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- スループット系テスト (`tests/test_e2e_webtransport_h3_throughput.py`) が通過し、性能劣化が無い

## 解決方法
