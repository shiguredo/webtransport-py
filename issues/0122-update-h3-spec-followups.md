# WebTransport over HTTP/3 の仕様追従の残りを対応する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/update-h3-spec-followups
- Polished: 2026-08-26

## 目的

draft-ietf-webtrans-http3-16 への追従のうち、細かい仕様逸脱・観測性の欠落として残っている項目をまとめて対応する。項目間の対象コード・仕様セクションは異なるが、いずれも draft-16 準拠のための小規模修正で、まとめて追従する意義がある。

## スコープと他 issue との切り分け

- `connect()` のタイムアウト・bounded 化は本 issue には含めない。h2/h3 双方の `Client.connect` 全体を bounded にして例外階層 (`ConnectTimeoutError` など) を導入する設計は 0046 で扱う (0046 は pending だが実装再開時に reopened する予定)
- Section 5 のセッションフロー制御 (WT_MAX_STREAMS / WT_MAX_DATA) は 0092 で扱う。本 issue の項目 2 (バッファリング上限) は Section 4.6 の受理前ストリーム / データグラム上限のみを対象とする

## 現状

- **WT_DRAIN_SESSION 受信経路がない**: draft-16 Section 4.7 の WT_DRAIN_SESSION (0x78ae) は `_deps/nghttp3/webtransport/source/lib/nghttp3_conn.c` のフォールスルーで IGN 状態に遷移するだけで、`src/bindings/webtransport_h3.cpp` にも受信ハンドラが存在しない (`WT_DRAIN` / `SessionDraining` / `drain_session` の grep ヒットゼロ)。h2 側は `src/bindings/webtransport_h2.cpp` の `handle_wt_drain_session` と `H2EventType::SessionDraining` で対応済みだが、h3 側にはアプリへの通知経路がない
- **バッファリング上限がない (Section 4.6 の範囲)**: draft-16 Section 4.6 は "To avoid resource exhaustion, endpoints MUST limit the number of buffered streams and datagrams" と規定する。しかし `H3Session::send_datagram` は `pending_datagrams_.push_back(std::move(datagram))` のみで上限チェックがなく、受理前ストリームバッファ (nghttp3 の WT_SESSION_BLOCKED 経由) も上限がない。`stream_buffers_` / `pending_sends_` / `pending_datagrams_` に上限を示す定数は存在しない
- **SETTINGS 受信判定が stream_id ハードコード**: `src/webtransport/h3/client.py` の `Client.connect` は「制御ストリーム (stream_id=3) にデータが届いたら SETTINGS 受信とみなす」実装 (`if quic_event.stream_id == 3: settings_received = True`) で、SETTINGS フレームの処理完了を確認していない。バインディング側の `recv_settings2_cb` は現状 `(void)settings; return 0;` の no-op で、SETTINGS 受信通知イベントは公開されていない
- **close() がピアの CONNECT ストリームクローズを待たない**: `src/webtransport/h3/client.py` の `Client.close` は WT_CLOSE_SESSION 送出直後に `self._quic_connection.close()` を呼ぶ。draft-16 Section 6 の "the endpoint SHOULD wait until all CONNECT streams have been closed by the peer before sending the CONNECTION_CLOSE" を満たさない。h2 側の `src/webtransport/h2/client.py` の `Client.close` も 10 回 × 10ms の固定スリープループにとどまり、half-close 完了状態を実際には検査していないため、両者とも仕様準拠していない

## 設計方針

### 項目 1: WT_DRAIN_SESSION の受信通知

- `src/bindings/webtransport_h3.cpp` に WT_DRAIN_SESSION (0x78ae) の受信解釈を追加する。nghttp3 側でフォールスルーする挙動を回避するため、バインディング側で当該カプセルを識別してハンドリングする (h2 側の `handle_wt_drain_session` と対称の実装)
- 新規イベント型 `H3EventType::SessionDraining` を追加する。`h3.pyi` の `EventType` にも `SESSION_DRAINING` を公開する
- 高レベル `h3.Client` に `on_session_draining` コールバックを追加する (h2 側と対称の API)
- 送信側 (`drain_session` メソッドの追加) は本 issue のスコープに含めない (受信通知のみ)。必要になれば別 issue で扱う

### 項目 2: バッファリング上限 (Section 4.6)

- `h3.Config` に以下 2 つの設定項目を追加する
  - `max_buffered_streams: int` — 受理前バッファに保持できるストリーム数の上限。デフォルトは 32 (実装時に暫定値。ドラフト実装の慣例に合わせる)
  - `max_buffered_datagrams: int` — 送信待ちデータグラムキューの上限。デフォルトは 128 (同上)
- 受理前ストリームは上限超過分を WT_BUFFERED_STREAM_REJECTED (draft-16 Section 4.6) で拒否する
- 送信待ちデータグラムキューは上限超過分を到着順に drop する (FIFO / LIFO ではなく enqueue 段階で drop)。破棄件数のログ出力を追加する
- 破棄・拒否はいずれも `H3Session::send_datagram` / 受理前ストリーム管理経路で行う。カプセル送出後の nghttp3 内部バッファは対象外 (本 issue のスコープは Session 層の受理前バッファに限定)

### 項目 3: SETTINGS 受信判定を stream_id ハードコードから直接観測へ

- `src/bindings/webtransport_h3.cpp` の `recv_settings2_cb` を実装する。SETTINGS 受信時に新規イベント型 `H3EventType::SettingsReceived` を発火する
- `h3.pyi` の `EventType` に `SETTINGS_RECEIVED` を公開する
- `src/webtransport/h3/client.py` の `Client.connect` から `if quic_event.stream_id == 3:` のハードコードを削除し、`h3.Session` の `SettingsReceived` イベントを観測する形に置き換える
- SETTINGS 受信待ちループの `max_attempts = 100` 固定はそのまま残す (bounded 化は 0046 の担当)。ただしループ側は SETTINGS 受信イベントの確認だけを行うようにする

### 項目 4: close() でピアの CONNECT ストリームクローズを実際に観測する

- `src/webtransport/h3/client.py` の `Client.close` を書き換え、WT_CLOSE_SESSION 送出後に CONNECT ストリームのピア側 FIN / STREAM_RESET の受信を実際に観測してから `self._quic_connection.close()` を呼ぶ
- 待機のタイムアウトはデフォルト 3 秒とする (実装時に確定)。期限までに閉じなければタイムアウトして `CONNECTION_CLOSE` を送出する (draft-16 Section 6 の "SHOULD wait" は義務ではないため、待機を諦めてクローズすることが許容される)
- h2 側の `src/webtransport/h2/client.py` の `Client.close` も同じ設計に置き換える (現状の 10 回 × 10ms 固定スリープは仕様準拠していないため、本 issue で対称修正する)
- 待機タイムアウトの設定は `h3.Client` / `h2.Client` のコンストラクタに引数として追加する (`close_wait_timeout` など)

## 完了条件

- 上記 4 項目がすべて対応され、それぞれ独立した PR として提出される (1 項目 1 PR)
- 各項目に対応するテストが追加される
  - 項目 1: WT_DRAIN_SESSION の受信で `on_session_draining` コールバックが発火する e2e テスト
  - 項目 2: 上限を超えたストリーム / データグラムが拒否・破棄される単体テスト
  - 項目 3: SETTINGS 受信イベントで判定が行われ、stream_id ハードコードに依存しない e2e テスト
  - 項目 4: h3 / h2 両方の close() がピアの CONNECT ストリームクローズを実際に待ってから CONNECTION_CLOSE を送出する e2e テスト (タイムアウト時の挙動も含む)
- `refs/webtrans/draft-ietf-webtrans-http3-16.txt` の該当セクション (4.6 / 4.7 / 6) の引用がコードコメントに残る (仕様由来である旨と将来変更の可能性を明記する。shiguredo-python 「仕様由来の機能を実装する場合は、根拠資料名・節番号・将来変更される可能性があることをコードコメントで明記すること」)
- 項目 3 の実装で `Client.connect` の SETTINGS 受信ループ内のハードコード (`stream_id == 3`) が完全に削除される
- 項目 4 で h2 側の 10 回 × 10ms 固定スリープループも撤去される
- 既存の全テストが通る

## 項目間の依存関係

- 項目 1 / 2 / 3 / 4 はいずれも独立して実装可能。1 PR ずつ順不同で進めてよい
- 項目 4 の h2 側修正は h2 側の close() の既存 e2e テストの期待値変更を伴う可能性があるため、h2 テストの修正を同 PR に含める
