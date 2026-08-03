# WebTransport over HTTP/3 の close_stream / reset_stream が送信バッファを削除しないのを修正する

- Created: 2026-08-02
- Completed: YYYY-MM-DD
- Branch: feature/fix-h3-stream-buffer-cleanup
- Polished: 2026-08-03

## 目的

リセットまたはセッション終了で破棄されたストリームの未送信データが接続終了までメモリに残る問題を修正する。送信バッファの解放は `acked_stream_data_cb` (ACK 受信時) にのみ任せられているが、`nghttp3_conn_add_ack_offset` / `nghttp3_conn_update_ack_offset` が呼ばれないため発火せず、どの経路でも解放されない。リセットやセッション終了で破棄されたストリームの未送信データが無駄にメモリを保持し続ける。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::close_stream` と `H3Session::reset_stream` は `stream_info_` からストリーム情報を削除するが、`stream_buffers_` は削除しない
- 送信バッファのデータの解放は `acked_stream_data_cb` (ACK 受信時) にのみ行われるが、`nghttp3_conn_add_ack_offset` / `nghttp3_conn_update_ack_offset` の呼び出しが `src/` に無いため発火しない。そのため未送信データが残り続ける (`stream_buffers_` のマップエントリ自体はどの経路でも削除されない)
- 対称に、`src/bindings/quic.cpp` の `QuicConnection::reset_stream` / `close_stream` や `src/bindings/http3.cpp` / `src/bindings/http2.cpp` / `src/bindings/webtransport_h2.cpp` は `stream_buffers_` から削除しており、h3 側だけ非対称になっている

## 設計方針

- `H3Session::close_stream` (reset_stream は close_stream に委譲するだけのため同箇所) で `stream_buffers_` からもストリームのエントリを削除する。削除のタイミングは `nghttp3_conn_close_stream` 呼び出しより前に行う (0009 の取り出し順序の制約と揃える)
- セッション終了経路の同種のリークも解消するため、`H3Session::close_session` と `H3Session::recv_wt_close_session_cb` がセッションに属するストリームを `stream_info_` から削除する際に、`stream_buffers_` からも削除する
- 対向からの CONNECT ストリームのリセットによるセッション終了 (draft-ietf-webtrans-http3-16 Section 6 のセッション終了条件の 1 つ目。0009 も正当な経路と明言) でも、セッションに属するデータストリームの送信バッファを削除する。nghttp3 はこの経路でデータストリームの stream_close コールバックを呼ばず (nghttp3_conn_abort_stream は stop_sending / reset_stream コールバックのみ)、さらに `nghttp3_conn_close_wt_session` は CONNECT ストリームが削除済みだと STREAM_NOT_FOUND を返すため close_session 経由の削除にも到達しない。そのため `H3Session::close_stream` / `reset_stream` にセッション ID (CONNECT ストリーム ID) が渡された場合は、セッションに属するデータストリームの `stream_buffers_` を `stream_info_` の走査で削除する。CONNECT ストリームであることの判定は `session_ids_` のメンバーシップで行う (セッション ID は CONNECT ストリーム ID そのものであり、`session_ids_` がそのレジストリを担う。0009 と同じ判定を使う)
- RFC 9000 Section 19.4「After sending a RESET_STREAM, an endpoint ceases transmission and retransmission of STREAM frames on the identified stream.」により、リセット後のバッファ破棄は仕様に適合する。セッション終了時のバッファ破棄は draft-ietf-webtrans-http3-16 Section 6 の「the endpoint MUST reset the send side and abort reading on the receive side of all unidirectional and bidirectional streams associated with the session」に適合する
- 完了条件の検証のため、テスト専用アクセサ (ストリームの送信バッファエントリの有無を返すメソッド。エントリが存在するストリームには True を、存在しないストリームには None を返す) を `H3Session` に追加する (nanobind バインディングと `src/webtransport/h3.pyi` も更新する。0009 で対象外とした h3.pyi の既存ドリフトは本 issue でも修正しない)。h3.pyi への追記はファイル末尾に追加し、0009 が変更する close_stream の行と離してマージの競合を避ける。アクセサはアンダースコア始まりの名前とし、恒久的な公開 API として扱わない
- 0009 (close_stream の戻り値変更) も同じ関数を変更対象とするため、実装順序によるマージの競合に注意する
- バッファの削除は close_stream / close_session / recv_wt_close_session_cb の各関数に明示的に集約する (`stream_close_cb` での削除は採用しない。nghttp3 のバージョンやストリーム種別により発火条件が異なり、削除経路として頼るのは不安定なため)
- リセット済みストリームへの再送信 (`send_stream_data`) によるバッファ再生成・正常 FIN 完了後の空エントリ残留 (この削除は 0013 の ACK 経路実装が担当する)・RESET_STREAM_AT への対応 (draft-ietf-webtrans-http3-16 Section 4.4 の MUST)・`stream_info_` 未登録のまま残ったバッファエントリ・`stream_info_` から削除済みのストリームのバッファエントリ (セッション終了経路の削除は `stream_info_` の走査によるため、`stream_info_` に存在するストリームに限定される)・nghttp3 の RESET_STREAM 送出要求 (reset_stream_cb) 時点のバッファ保持 (ストリームが閉じていないため正しい挙動)・CONNECT ストリームのクリーンクローズ (FIN) によるセッション終了 (高レベル層は STREAM_RESET イベントでのみ `close_stream` を呼び、`H3Session` は end_stream コールバックを登録していないため、FIN ではセッション終了の検知自体が発生しない。検知経路の追加は本 issue の対象外とする) は対象外とする
- 0013 (ACK 経路の解放) との相互作用に注意する。0013 実装後は `nghttp3_conn_add_ack_offset` が呼ばれ `acked_stream_data_cb` が発火するため、本 issue の完了条件テスト (リセット経路・セッション終了経路・CONNECT ストリームのリセット経路のすべて) は「送信処理を挟むと ACK でバッファエントリが解放されて検証が空虚になる」前提で記述する。具体的には、バッファエントリを生成した後は送信処理 (受信側の `get_streams_to_send` / QUIC 送信を含む) を挟まずに削除経路を処理し、アクセサでの確認は送信処理より前に行う
- 実装着手前に、nghttp3 の webtransport ブランチを `recv_wt_close_session` コールバックを含む最新版に更新する必要がある (現在の `_deps/nghttp3` のキャッシュには存在せず、現状のコードはビルドできない)。更新先は、`recv_wt_close_session_cb` がデータストリームのストリーム削除より前に呼ばれる順序を維持している版を選ぶ (設計の成立条件。現行の webtransport ブランチはこの順序であることを確認済み)

## 完了条件

- リセットまたはセッション終了で破棄されたストリームの送信バッファが削除される (テスト専用アクセサで確認する)
- モックなしのテストで検証できる:
  - リセット経路: 両側を低レベル API (`quic.Connection` + `h3.Session`) で構築し、検証対象側で事前に `send_stream_data` してバッファエントリを生成した状態でリセットし、アクセサでバッファエントリが削除されたこと (None) と接続が維持されることを確認する (リセット前に送信しておかないと、削除されるべきバッファエントリが存在しない状態での検証になってしまう。リセット前に送信処理 (`get_streams_to_send` / QUIC 送信) を挟むと、0013 実装後は ACK 処理でバッファエントリが解放されてしまい、本修正の削除を検証できないため、送信処理を挟まずにリセットする)
  - セッション終了経路: 両側を低レベル API (`quic.Connection` + `h3.Session`) で構築し、`close_session` 呼び出し側と WT_CLOSE_SESSION 受信側の両方で事前に `send_stream_data` してバッファエントリを生成した状態で、`close_session` 呼び出しと WT_CLOSE_SESSION 受信を処理し、両側のセッションに属するストリームのバッファエントリが削除されたこと (None) をアクセサで確認する (高レベル `Server` には close_session メソッドが無いため。両側で送信しておかないと、`close_session` 呼び出し側と WT_CLOSE_SESSION 受信側のどちらか一方の削除経路が実データなしでしか検証できない)。バッファエントリを生成した後は、WT_CLOSE_SESSION 受信処理 (`recv_wt_close_session_cb`) より前に自身の送信処理を挟まない (0013 実装後は送信処理で `acked_stream_data_cb` が発火し、`recv_wt_close_session_cb` での削除を検証する前にエントリが消えるため)。CONNECT ストリームのリセット経路 (セッション ID に対する `close_stream` / `reset_stream`) も同じ前提で、セッションに属するデータストリームのバッファエントリが削除されたこと (None) をアクセサで確認する。なお、削除は `stream_info_` の走査をセッション ID で絞り込む設計のため、複数セッションを張った状態で対象セッションのバッファのみが削除され、他セッションのバッファエントリが残ることもアクセサで確認する
