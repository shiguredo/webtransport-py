# WebTransport over HTTP/3 で QPACK デコードブロック中の受理前 CONNECT ストリームに DATA フレームが pipeline されるとヒープ破壊で SIGABRT する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-qpack-blocked-data-heap-corruption
- Polished: 2026-09-07

## 目的

WebTransport over HTTP/3 サーバーで、クライアントが正当なワイヤ列 (`headers + DATA フレーム + 任意のカプセル`) を送るだけでサーバープロセスが SIGABRT する経路がある。QPACK 動的テーブル参照でヘッダーがブロックされている状態の受理前 CONNECT ストリームに DATA フレームが後続すると、後で QPACK エンコーダーストリームを受信して unblock した瞬間に nghttp3 内部でヒープ破壊 (`pointer being freed was not allocated`) が起きる。リモートから発火可能なクラッシュのため、正式リリース前の最優先ブロッカーとして修正する。

## 現状

- `src/webtransport/h3/server.py` の `Server._process_quic_events` は QUIC の `STREAM_DATA` イベントを `H3Session.receive_stream_data(stream_id, data, fin)` に無条件で投入する
- 既定 `H3SessionConfig::qpack_blocked_streams = 100` (`src/bindings/webtransport_h3.h`) のため、動的テーブル参照を含むヘッダーは QPACK エンコーダー受信までブロックされる
- QUIC はクロスストリーム順序を保証しないため、攻撃者は「エンコーダー挿入を後、CONNECT ヘッダー + DATA フレームを先」の順で送出できる
- 実験 (scratchpad `exp4c_variants.py`) で 4 バリアントを検証: `headers + FIN` のみ正常、`headers + DATA + FIN` / `headers + WT_CLOSE_SESSION + FIN` / `headers + WT_CLOSE_SESSION` (FIN なし) の 3 バリアントが終了コード 134 (SIGABRT)
- クラッシュのバックトレース: `nghttp3_conn_read_qpack_encoder + 356 ← nghttp3_conn_read_uni ← nghttp3_conn_read_stream2` (`_deps/nghttp3/webtransport/source/lib/nghttp3_conn.c`)。FIN の有無・カプセル種別・UTF-8 の妥当性はすべて無関係
- nghttp3 (webtransport ブランチ) は QPACK デコードブロック検知時に、フィールドセクションより後ろの残バイト (DATA フレーム等) をストリーム内部の inq バッファへ取り込み、ブロック解除時の再処理 (`nghttp3_conn_read_qpack_encoder` → `nghttp3_conn_process_blocked_stream_data`) で parse する。解除時に inq へヘッダー以外のデータが存在すると異常挙動 (クラッシュ / 無限ループ) が発生することは、closed issue 0064 が「同一読み取り・別読み取りを問わず」実測で確認しており、0064 はこの経路を既知の制約として対象外にした (本 issue がこの制約の解消を担う)
- 既存 `tests/test_webtransport_h3_qpack_blocked_pre_accept_fin.py` は headers + FIN の受理前 FIN 経路 5 関数 (同一読み取り / 別読み取りのパラメータ化を含め 6 ケース) をカバーするが、後続に DATA フレームが続く経路は 1 件も無い

## 設計方針

- CODEBASE.md「nghttp3 をフォークしないこと。依存ライブラリの改修が必要な機能は、ライブラリ側の対応を待つか、バインディング層で吸収できる設計にすること」に従う
- バインディング層 (`src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` / `begin_headers_cb` / `end_headers_cb`) に **フレーム境界を把握した状態ベースのガード** を入れる。nghttp3 はブロック検知時点でフィールドセクション以降のバイトを自前の inq へ取り込むため、「ブロック確定後に届いたチャンクを保留する」だけでは、HEADERS フレームと DATA フレームが同じ `receive_stream_data` 呼び出し (同じ QUIC STREAM_DATA イベント) に混在するケースを防げない (0064 の実測どおり同一読み取りでも異常挙動する)
  - 新規のクライアント起点双方向ストリームの先頭バイトから HTTP/3 フレーム種別 (varint) とフレーム長 (varint) を解釈し、先頭フレームが HEADERS (0x01) の場合は **HEADERS フレーム全体だけを `nghttp3_conn_read_stream2` に渡し、フレーム境界より後ろのバイトをバインディング側で保持する**。先頭フレームが HEADERS でないストリーム (WebTransport データストリームの extended frame 等) は現行どおり一括で渡す。フレームヘッダー (種別・長さの varint) が STREAM_DATA イベントを跨いで分割されるケースに備え、フレームヘッダーの解釈状態をストリーム単位で保持する
  - 保持したバイトは、当該ストリームの `end_headers_cb` 発火後 (QPACK デコード完了後) に nghttp3 へ投入する。`begin_headers_cb` 発火済み・`end_headers_cb` 未発火 (＝ `pending_headers_` に含まれる) を QPACK デコードブロック中として扱い、その間は新規に届いたバイトもすべて保持する。データ長 0 の FIN イベントは保持対象外とし、既存どおり fin 付きで nghttp3 へ通す (0064 の fin 引数検知が成立する状態を維持する)
  - これにより nghttp3 へ渡るのは「フィールドセクションまでが未完で inq に入る」状態 (ヘッダー単体) に限定され、解除後の再処理で inq にヘッダー以外のデータが混在する異常経路をバインディング側で排除できる
- 保持は `H3Session::pending_qpack_blocked_fin_stream_ids_` と同様の per-stream 管理とし、既存の受理前 FIN 保留パターンと整合させる。ストリーム ID だけでなく、保持するバイト列・fin・フレーム境界の解釈状態をストリーム単位で持つ必要がある。保持解除時に順序を保って投入し、fin 付きで保持したデータを投入するときも fin 引数を維持する
- 引数ベースの検証では防げない (正当なワイヤ列で発火する) ため、ストリーム数制限 (`set_max_client_streams_bidi` 等) などの既存ガードとは別の状態ベースの追加が必要
- 同時に nghttp3 上流 (`ngtcp2/nghttp3` の webtransport ブランチ) に再現ケースを issue として報告する。上流修正が入るまではバインディング側のガードを継続する

## 完了条件

- 上記 3 バリアント (`data_then_fin` / `wtclose_then_fin` / `wtclose_no_fin`) が、HEADERS と後続データの同一読み取り・別読み取りのどちらでもサーバーが SIGABRT せず、無限ループでハングもしないこと
- QPACK ブロック解除後に保持していたデータが正しく nghttp3 に投入され、ブロックが無い場合 (QPACK エンコーダーストリームの方が先に届いた場合) と同一のイベント列・同一回数で処理されること (`SessionReady` / `SessionClosed` の発火回数と `error_code` に差異が無いこと。`SessionClosed` が二重に発火しないこと)。DATA を伴わない通常のセッション確立は影響を受けないこと
- `tests/test_webtransport_h3_qpack_blocked_pre_accept_fin.py` に、QPACK ブロック中に DATA フレーム / WT_CLOSE_SESSION が後続する受理前経路の Sans-IO テストを追加する (同一読み取り・別読み取り、カプセル種別、FIN の有無を含む。モックやスタブは使わない)。`RuleBasedStateMachine` によるステートフル PBT は open issue 0187 が `prop_h3_qpack_blocked_pipelined_data_no_abort` として予定済みのため重複実装せず、本 issue は上記の決定的テストを回帰ピンとする
- 既存のテスト全 822 件が引き続き通過すること
