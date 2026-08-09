# CONNECTION_CLOSE の再送が CLOSING 期間満了後も無期限に続く

- Created: 2026-08-09
- Completed: 2026-08-10
- Branch: feature/fix-connection-close-retransmission-limit
- Polished: 2026-08-09

## 目的

close() 後の受信パケットへの応答として CONNECTION_CLOSE を再送する実装 (closed issue 0031) で、再送が CLOSING 期間 (RFC 9000 Section 10.2 の「at least three times the current PTO interval」) 満了後も無期限に続く問題を修正する。

## 現状

- `src/bindings/quic.cpp` の `receive` メソッドは、close() で CONNECTION_CLOSE を生成できた接続に対して closed_ 後も受信パケットを処理し、`NGTCP2_ERR_CLOSING` を受けるたびに `close_packet_armed_` を true に戻して再送する
- `src/bindings/quic.cpp` の `get_timeout_ns` / `handle_timeout` は `closed_` ガードで早期 return するため、CLOSING 期間 (約 3×PTO) の満了がアプリ層で処理されない
- その結果、ピアが送信を続ける限り、同一 CONNECTION_CLOSE を無期限に再送し続ける。1:1 応答で増幅にはならないが、無制限の応答は closing 状態の有界性という RFC の設計意図に反する
- RFC 9000 Section 10.2 は closing / draining 状態を「SHOULD persist for at least three times the current PTO interval」とし、状態終了後は「SHOULD discard all connection state」のうえで「MAY send a Stateless Reset in response to any further incoming packets」と定める
- ngtcp2 は CLOSING 期間の満了時刻を管理しない (`ngtcp2_conn_get_expiry` の返す期限に CLOSING 期間項は無く、`in_closing_period()` は connection state が CLOSING であることだけを返す)。満了判定はバインディング側で持つ必要がある

## 設計方針

- close() が CONNECTION_CLOSE を生成できた場合 (保持パケットが存在する場合) に限り、バインディング側で close() 時刻 + 3×PTO (close() 時点の PTO を固定) を満了時刻として保持し、CLOSING 期間を管理する。3×PTO は RFC の下界であり、これを下回る満了にしてはならない。PTO は close() 内で `closed_` を立てる前に `ngtcp2_conn_get_pto2` を直接呼んで取得する (公開アクセサの pto() は closed_ 後に nullopt を返すため)。満了判定は `get_timeout_ns` / `handle_timeout` / `receive` / `send` が共有する
- `get_timeout_ns` は保持パケットが存在する closing 中は満了までの残り時間のみを返し、満了時刻には 0 を返して `handle_timeout` の呼び出しを促し、破棄後は nullopt に戻す (保持パケットが無いため既存の `closed_` ガードどおり)。ngtcp2 固有の expiry (PTO / idle 等) は返さない
- `handle_timeout` は満了時刻を過ぎた場合のみ保持パケット (pending_close_packet_) を破棄して再送を停止し、満了前の呼び出しでは破棄しない。破棄は初回配送が完了した後のみ行い、初回配送前の満了では破棄しないで最初の `send()` が CONNECTION_CLOSE を返せる状態を保つ。破棄後は armed 判定も初回配送判定も参照されない (ngtcp2 は駆動しない。駆動すると idle_timeout が 3×PTO より短い場合に idle timeout イベントが発生し得る)
- 満了後の再送停止は `handle_timeout` の呼び出しに依存させない。`receive` と `send` は満了時刻を独立に判定し、満了後の `receive` は受信パケットを ngtcp2 に渡さず 0 を返して再アームも ConnectionClosed イベントの push もせず (0031 の「close() 起因の closing ではイベントを push しない」契約を維持する)、満了後の `send` は再アームされた CONNECTION_CLOSE を返さない
- close() 直後の最初の `send()` (初回配送) は満了判定の対象外とし、0030 / 0031 の「初回は必ず返し」契約を維持する。初回配送前の満了後は破棄が遅延するため `get_timeout()` が 0 を返し続け、`handle_timeout()` は no-op になる (初回配送はアプリの `send()` 呼び出しで行われる。初回配送完了後に `handle_timeout()` が呼ばれると破棄され nullopt に戻る)。初回配送の遅延で最初の送出から満了までの実効再送窓は短縮されるが、closing 状態の持続期間は close() から 3×PTO で維持される
- `closed_` ガードの緩和は「保持パケットが存在する場合」に限定し、保持パケットが無い接続 (RETRY 経路等) の「閉じた後は get_timeout() が None」という既存契約を維持する
- 満了後は `send()` が再アームされた CONNECTION_CLOSE を返さず、受信パケットにも応答しないことを確認する。QUIC 接続状態の完全破棄 (conn_ の解放) はスコープ外とする (closed_ 後も多数の getter が conn_ を参照するため)。`in_closing_period()` は ngtcp2 の state を返すため満了後も true のままであり、本 issue では変更しない
- 0031 の「再送レート制限のためのタイマーは導入しない」判断は維持する (本 issue が導入するのは CLOSING 期間の満了管理であり、レート制限ではない)。DRAINING 状態はスコープ外とする (DRAINING では MUST NOT send のため再送問題は発生しない)
- 高レベル API は close() 後に受信ループを回さないため、本修正の効果は低レベル API (Sans-IO) に限られる (closed issue 0031 と同じ線引き)
- 変更対象は `src/bindings/quic.cpp` (receive / send / close / get_timeout_ns / handle_timeout / ムーブコンストラクタ・ムーブ代入演算子)、`src/bindings/quic.h` (get_timeout_ns / receive / send のドキュメント更新、満了管理用の状態保持 (満了時刻・初回配送完了フラグ))、テスト (tests/test_quic_error_handling.py に再送停止のテストを追加)、CHANGES.md (## develop セクションへの [FIX] エントリ)

## 完了条件

- CLOSING 期間満了後、ピアがパケットを送り続けても CONNECTION_CLOSE が再送されない
- CLOSING 期間内は従来どおり受信パケットごとに 1 回再送される
- 満了後の `send()` は再アームされた CONNECTION_CLOSE を返さない (初回配送は満了判定の対象外)
- 満了後、`handle_timeout()` を呼ぶ前 (保持パケット破棄前) でも、満了後にピアのパケットを受信しても CONNECTION_CLOSE が再送されない (再送停止が破棄に依存しない)
- close() 後に初回 `send()` を満了まで遅延しても、最初の `send()` が CONNECTION_CLOSE を返す (初回配送契約の維持)
- 満了処理後 (保持パケット破棄後) は `get_timeout()` が None に戻る
- 保持パケットが無い接続 (RETRY 経路等) では「閉じた後は get_timeout() が None」の既存契約が維持される
- モックなしの Sans-IO テストで確認する (時間は実時間で進むため、CLOSING 期間の経過は get_timeout() の返り値に応じた実時間待ちで再現する。テストはハンドシェイク完了後に close() した接続で行う。ハンドシェイク途中の close() では PTO が初期 PTO 相当になり実時間待ちが大きく伸びるため。手順: close() → 初回 send() で CONNECTION_CLOSE を送出 (初回配送を先に完了) → 満了前にピアのパケットを受信して再アーム → 実時間待ちで満了 → handle_timeout() を呼ぶ前に満了後受信パケットを渡して send() が None を返すこと (満了前に再アーム済みのパケットも含め、満了後の send() が再送しないこと。破棄に依存しないことを確認) → handle_timeout() で破棄され get_timeout() が None に戻る)

## 解決方法

- `src/bindings/quic.h` に CLOSING 期間の満了管理用の状態保持 (`closing_expiry_ns_`: 満了時刻、`close_packet_delivered_once_`: 初回配送完了フラグ) を追加し、`receive` / `send` / `get_timeout_ns` / `handle_timeout` のドキュメントを更新した
- `src/bindings/quic.cpp` の `close` メソッドは、CONNECTION_CLOSE を生成できた場合に限り、close() 時刻 + 3×PTO (close() 時点の PTO を `ngtcp2_conn_get_pto2` で固定) を満了時刻として保持するようにした (RFC 9000 Section 10.2 の「at least three times the current PTO interval」)
- `src/bindings/quic.cpp` の `get_timeout_ns` は、維持パケットが存在する closing 中は CLOSING 期間の満了までの残り時間のみを返し、満了後は 0、破棄後は nullopt に戻すようにした
- `src/bindings/quic.cpp` の `handle_timeout` は、満了時刻を過ぎた場合のみ保持パケットを破棄して再送を停止するようにした。破棄は初回配送完了後のみ行い、初回配送前の満了では破棄しない (最初の send() が CONNECTION_CLOSE を返せる状態を保つ)。closing 中は ngtcp2 を駆動しない
- `src/bindings/quic.cpp` の `receive` は、満了後は受信パケットを ngtcp2 に渡さず 0 を返して再アームもイベント push も行わないようにした (再送停止が破棄に依存しない)
- `src/bindings/quic.cpp` の `send` は、初回配送 (close() 直後の最初の send()) は満了判定の対象外で必ず返し、満了後は再アームされていても返さないようにした
- `src/bindings/quic.cpp` のムーブコンストラクタ・ムーブ代入演算子に新メンバーを追加した
- `tests/test_quic_error_handling.py` に `test_connection_close_retransmission_stops_after_closing_period` (満了後は再送が停止し、破棄に依存しないこと・満了処理後は get_timeout() が None に戻ることを確認) と `test_connection_close_first_send_after_closing_period` (初回配送は満了判定の対象外・初回配送前の満了では破棄されないことを確認) を追加した
- `CHANGES.md` の `## develop` セクションに `[FIX]` エントリを追加した
