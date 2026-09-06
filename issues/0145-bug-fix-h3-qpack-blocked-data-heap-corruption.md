# WebTransport over HTTP/3 で QPACK デコードブロック中の受理前 CONNECT ストリームに DATA フレームが pipeline されるとヒープ破壊で SIGABRT する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-qpack-blocked-data-heap-corruption
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/3 サーバーで、クライアントが正当なワイヤ列 (`headers + DATA フレーム + 任意のカプセル`) を送るだけでサーバープロセスが SIGABRT する経路がある。QPACK 動的テーブル参照でヘッダーがブロックされている状態の受理前 CONNECT ストリームに DATA フレームが後続すると、後で QPACK エンコーダーストリームを受信して unblock した瞬間に nghttp3 内部でヒープ破壊 (`pointer being freed was not allocated`) が起きる。リモートから発火可能なクラッシュのため、正式リリース前の最優先ブロッカーとして修正する。

## 現状

- `src/webtransport/h3/server.py` の `Server._process_quic_events` は QUIC の `STREAM_DATA` イベントを `H3Session.receive_stream_data(stream_id, data, fin)` に無条件で投入する
- 既定 `H3SessionConfig::qpack_blocked_streams = 100` (`src/bindings/webtransport_h3.h`) のため、動的テーブル参照を含むヘッダーは QPACK エンコーダー受信までブロックされる
- QUIC はクロスストリーム順序を保証しないため、攻撃者は「エンコーダー挿入を後、CONNECT ヘッダー + DATA フレームを先」の順で送出できる
- 実験 (scratchpad `exp4c_variants.py`) で 4 バリアントを検証: `headers + FIN` のみ正常、`headers + DATA + FIN` / `headers + WT_CLOSE_SESSION + FIN` / `headers + WT_CLOSE_SESSION` (FIN なし) の 3 バリアントが終了コード 134 (SIGABRT)
- クラッシュのバックトレース: `nghttp3_conn_read_qpack_encoder + 356 ← nghttp3_conn_read_uni ← nghttp3_conn_read_stream2` (`_deps/nghttp3/webtransport/source/lib/nghttp3_conn.c`)。FIN の有無・カプセル種別・UTF-8 の妥当性はすべて無関係
- 既存 `tests/test_webtransport_h3_qpack_blocked_pre_accept_fin.py` は headers + FIN の受理前 FIN 経路 5 ケースをカバーするが、DATA フレームが後続する経路は 1 件も無い

## 設計方針

- CODEBASE.md「nghttp3 をフォークしないこと。依存ライブラリの改修が必要な機能は、ライブラリ側の対応を待つか、バインディング層で吸収できる設計にすること」に従う
- バインディング層 (`src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` / `begin_headers_cb` / `end_headers_cb`) に **状態ベースのガード** を入れる: サーバー側の受理前 CONNECT ストリームが QPACK デコードブロック中 (begin_headers 発火済み・end_headers 未発火) である間、後続の HTTP/3 DATA フレームを nghttp3 に渡さずバインディング側で保留し、unblock 後に nghttp3 へ投入する
- 保留は `H3Session::pending_qpack_blocked_fin_stream_ids_` と同様の per-stream 集合で管理する。既存の受理前 FIN 保留パターンと整合させる
- 引数ベースの検証では防げない (正当なワイヤ列で発火する) ため、既存の `set_max_client_streams_bidi` 等のガードとは別の状態ベースの追加が必要
- 同時に nghttp3 上流 (`ngtcp2/nghttp3` の webtransport ブランチ) に再現ケースを issue として報告する。上流修正が入るまではバインディング側のガードを継続する

## 完了条件

- 上記 3 バリアント (`data_then_fin` / `wtclose_then_fin` / `wtclose_no_fin`) を含む receive 順序でサーバーが SIGABRT しないこと
- QPACK ブロック解除後に保留していた DATA フレームが正しく nghttp3 に投入され、`SessionReady` / `SessionClosed` 等のイベントが正常に発火すること
- `tests/prop_webtransport_h3.py` に `RuleBasedStateMachine` によるステートフル PBT を追加し、`headers` / `DATA` / `encoder-stream` / `FIN` の任意順序で abort しないことを回帰ピンとして保証すること
- 既存のテスト全 822 件が引き続き通過すること
