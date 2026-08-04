# WebTransport over HTTP/3 の close_stream / reset_stream / close_session が送信バッファを削除しないのを修正する

- Created: 2026-08-02
- Completed: 2026-08-04
- Branch: feature/fix-h3-stream-buffer-cleanup
- Polished: 2026-08-04

## 目的

リセットまたはセッション終了で破棄されたストリームの未送信データが接続終了までメモリに残る問題を修正する。送信バッファの解放は ACK 経路 (0013 で実装済み) にのみ任せられており、リセットやセッション終了で破棄されたストリームの未送信データはどの経路でも解放されず、無駄にメモリを保持し続ける。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::close_stream` と `H3Session::reset_stream` は `stream_info_` からストリーム情報を削除するが、`stream_buffers_` は削除しない
- 送信バッファのデータの解放は ACK 経路 (`get_streams_to_send` での `nghttp3_conn_add_ack_offset` 呼び出しによる `acked_stream_data_cb` 発火。0013 で実装済み) にのみ行われるため、リセットやセッション終了で破棄されたストリームの未送信バッファは解放されない (`stream_buffers_` のマップエントリは close_stream / reset_stream / close_session / recv_wt_close_session_cb / stream_close_cb のどの経路でも削除されない)
- 対称に、`src/bindings/quic.cpp` の `QuicConnection::reset_stream` / `close_stream` や `src/bindings/http3.cpp` / `src/bindings/http2.cpp` / `src/bindings/webtransport_h2.cpp` は `stream_buffers_` から削除しており、h3 側だけ非対称になっている

## 設計方針

- `H3Session::close_stream` (reset_stream は close_stream に委譲するだけのため同箇所) で `stream_buffers_` からもストリームのエントリを削除する。削除のタイミングは `nghttp3_conn_close_stream` 呼び出しより前に行う (同期コールバックは `stream_buffers_` を参照しないため前後どちらでも動作するが、0009 が `stream_info_` の取り出しを nghttp3 呼び出し前に行うのと同様に、nghttp3 呼び出し前に片付ける)
- セッション終了経路の同種のリークも解消するため、`H3Session::close_session` と `H3Session::recv_wt_close_session_cb` がセッションに属するストリームを `stream_info_` から削除する際に、`stream_buffers_` からも削除する
- 対向からの CONNECT ストリームのリセットによるセッション終了 (draft-ietf-webtrans-http3-16 Section 6 のセッション終了条件の 1 つ目。0009 も正当な経路と明言) でも、セッションに属するデータストリームの送信バッファを削除する。nghttp3 はこの経路でデータストリームの stream_close コールバックを呼ばず (nghttp3_conn_abort_stream は stop_sending / reset_stream コールバックのみ)、さらに `nghttp3_conn_close_wt_session` は CONNECT ストリームが削除済みだと STREAM_NOT_FOUND を返すため close_session 経由の削除にも到達しない。そのため `H3Session::close_stream` / `reset_stream` にセッション ID (CONNECT ストリーム ID) が渡された場合は、セッションに属するデータストリームの `stream_buffers_` を `stream_info_` の走査で削除する (データストリームの `stream_info_` エントリ自体は削除しない。先に削除すると、`nghttp3_conn_close_stream` の同期コールバックで発火する reset_stream_cb / stop_sending_cb のセッション ID 取得ができなくなる)。両側の STREAM_RESET ハンドラ (高レベル `Client` / `Server`) は QUIC の STREAM_RESET イベントの stream_id をそのまま `close_stream` に渡すため、CONNECT ストリームのリセット時は自動的にセッション ID が渡る (0009 の close_stream 戻り値変更には依存しない)。CONNECT ストリームであることの判定は `session_ids_` のメンバーシップで行う (セッション ID は CONNECT ストリーム ID そのものであり、`session_ids_` がそのレジストリを担う。0009 と同じ判定を使う)
- RFC 9000 Section 19.4「After sending a RESET_STREAM, an endpoint ceases transmission and retransmission of STREAM frames on the identified stream.」により、リセット後のバッファ破棄は仕様に反しない (破棄は MUST / SHOULD / MAY のいずれでも規定されていないが、再送信が停止するため未送信データを保持する義務がなくなる)。セッション終了時のバッファ破棄は draft-ietf-webtrans-http3-16 Section 6 の「the endpoint MUST reset the send side and abort reading on the receive side of all unidirectional and bidirectional streams associated with the session」に適合する
- 完了条件の検証には、0013 で追加済みのテスト専用アクセサ `_has_stream_buffer` (ストリームの送信バッファエントリの有無を返す。エントリが存在するストリームには True を、存在しないストリームには None を返す) を使用する (アンダースコア始まりの名前であり、恒久的な公開 API として扱わない。`src/webtransport/h3.pyi` はビルド時に nanobind が自動生成する成果物であり git 追跡対象外のため手編集しない)
- 0009 (close_stream の戻り値変更) も同じ関数を変更対象とするため、実装順序によるマージの競合に注意する。実装順序はどちらが先でも成立する (本 issue の CONNECT ストリーム判定は既存の `session_ids_` を使用し、0009 の close_stream 戻り値変更には依存しない)
- バッファの削除は close_stream / close_session / recv_wt_close_session_cb の各関数に明示的に集約する (`stream_close_cb` での削除は採用しない。nghttp3 のバージョンやストリーム種別により発火条件が異なり、削除経路として頼るのは不安定なため)
- リセット済みストリームへの再送信 (`send_stream_data`) によるバッファ再生成・正常 FIN 完了後の空エントリ残留 (0013 で実装済み)・RESET_STREAM_AT への対応 (draft-ietf-webtrans-http3-16 Section 4.4 の MUST。現状はリセット時に RESET_STREAM_AT を送出しておらず、ストリームヘッダーが失われてセッション復元ができない。対応が必要になったら別 issue とする)・`stream_info_` 未登録のまま残ったバッファエントリ・`stream_info_` から削除済みのストリームのバッファエントリ (セッション終了経路の削除は `stream_info_` の走査によるため、`stream_info_` に存在するストリームに限定される)・nghttp3 の RESET_STREAM 送出要求 (reset_stream_cb) 時点のバッファ保持 (ストリームが閉じていないため正しい挙動。高レベル層は RESET_STREAM イベントで `quic_connection.reset_stream` のみを呼び h3 層の close_stream / reset_stream を呼ばないため、この経路のバッファはその後も解放されず接続終了まで残る)・CONNECT ストリームのクリーンクローズ (FIN) によるセッション終了 (高レベル層は STREAM_RESET イベントでのみ `close_stream` を呼び、`H3Session` は end_stream コールバックを登録していないため、FIN ではセッション終了の検知自体が発生しない。検知経路の追加は本 issue の対象外とする) は対象外とする
- 0013 (ACK 経路の解放) との相互作用に注意する。0013 実装済みのため `get_streams_to_send` は `nghttp3_conn_add_ack_offset` を呼び、送信処理を挟むと `acked_stream_data_cb` が発火してバッファエントリが解放される。本 issue の完了条件テスト (リセット経路・セッション終了経路・CONNECT ストリームのリセット経路のすべて) は「送信処理を挟むと ACK でバッファエントリが解放されて検証が空虚になる」前提で記述する。具体的には、バッファエントリを生成した後は送信処理 (両側の `get_streams_to_send` / QUIC 送信) を挟まずに削除経路を処理し、アクセサでの確認は送信処理より前に行う
- 設計の成立条件として、nghttp3 の webtransport ブランチは `recv_wt_close_session_cb` がデータストリームの reset_stream_cb / stop_sending_cb 発火より前に呼ばれる順序である必要がある (この経路ではデータストリームの stream_close は発生しないが、コールバックの時点で `stream_info_` が intact であることが走査削除の成立条件になる。現行の `_deps/nghttp3` のキャッシュはこの順序であることを確認済み。ブランチ更新時はこの順序を維持していることを確認する)
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (close_stream / close_session / recv_wt_close_session_cb のバッファ削除) と `tests/test_webtransport_h3_stream_buffer_cleanup.py` (新規テスト)。`src/webtransport/h3.pyi` はビルド時に nanobind が自動生成する成果物であり git 追跡対象外のため変更しない

## 完了条件

- リセットまたはセッション終了で破棄されたストリームの送信バッファが削除される (`_has_stream_buffer` アクセサで確認する)
- モックなしのテストで検証できる (テストは `tests/test_webtransport_h3_stream_buffer_cleanup.py` に追加し、0013 と同じ h3.Session 同士の直接受け渡し構成で構築する):
  - リセット経路: h3.Session 同士を直接受け渡しで構築し (0013 の `tests/test_webtransport_h3_ack_offset.py` と同じ構成。`get_streams_to_send` の出力を `receive_stream_data` で渡す)、検証対象側で事前に `send_stream_data` してバッファエントリを生成した状態でリセットし、アクセサでバッファエントリが削除されたこと (None) と接続が維持されること (`is_closed()` が False) を確認する (リセット前に送信しておかないと、削除されるべきバッファエントリが存在しない状態での検証になってしまう。リセット前に送信処理 (`get_streams_to_send` / QUIC 送信) を挟むと、0013 実装済みの ACK 処理でバッファエントリが解放されてしまい、本修正の削除を検証できないため、送信処理を挟まずにリセットする。リセットは h3 層の `close_stream` / `reset_stream` を直接呼ぶ形でよい (対向からの QUIC RESET_STREAM 受信経由でも同一関数が呼ばれるため))
  - セッション終了経路: 同じく h3.Session 同士の直接受け渡しで構築し、`close_session` 呼び出し側と WT_CLOSE_SESSION 受信側の両方で事前に `send_stream_data` してバッファエントリを生成した状態で、`close_session` 呼び出しと WT_CLOSE_SESSION 受信を処理し、両側のセッションに属するストリームのバッファエントリが削除されたこと (None) をアクセサで確認する (両側で送信しておかないと、`close_session` 呼び出し側と WT_CLOSE_SESSION 受信側のどちらか一方の削除経路が実データなしでしか検証できない。なお高レベル `Server` には close_session メソッドが無いため、セッション終了経路のテストは低レベル API で行う)。バッファエントリを生成した後は、WT_CLOSE_SESSION 受信処理 (`recv_wt_close_session_cb`) より前に自身の送信処理を挟まない (0013 実装済みのため送信処理で `acked_stream_data_cb` が発火し、`recv_wt_close_session_cb` での削除を検証する前にエントリが消えるため)。CONNECT ストリームのリセット経路 (セッション ID に対する `close_stream` / `reset_stream`) も同じ前提で、セッションに属するデータストリームのバッファエントリが削除されたこと (None) をアクセサで確認する。なお、削除は `stream_info_` の走査をセッション ID で絞り込む設計のため、複数セッションを張った状態で対象セッションのバッファのみが削除され、他セッションのバッファエントリが残ることもアクセサで確認する (0013 の `_establish_session` 相当は 1 セッション構成のため、2 本目の CONNECT を確立する拡張が必要)

## 解決方法

`src/bindings/webtransport_h3.cpp` の送信バッファ (`stream_buffers_`) の削除を、リセット・セッション終了の各経路に明示的に追加した。

- `H3Session::close_stream` (reset_stream は委譲のため同箇所) で、`nghttp3_conn_close_stream` 呼び出しより前に送信バッファを削除するようにした。通常のデータストリームは該当ストリームのエントリを、CONNECT ストリーム (セッション ID) のリセットではセッションに属するデータストリームのエントリを `stream_info_` の走査で削除する。CONNECT ストリームの場合は `stream_info_` エントリ自体は削除しない (同期コールバックで発火する reset_stream_cb / stop_sending_cb のセッション ID 取得に必要)
- `H3Session::close_session` と `H3Session::recv_wt_close_session_cb` のセッション所属ストリームのクリーンアップに送信バッファの削除を追加した。共通処理はプライベートヘルパー `erase_session_streams` に集約した

テストは `tests/test_webtransport_h3_stream_buffer_cleanup.py` に追加した。h3.Session 同士の直接受け渡し構成 (モックなし) で、送信処理を挟まない前提 (ACK 経路での解放を避ける) の下で検証する。

- `test_reset_releases_send_buffer` (close_stream / reset_stream の 2 経路): リセットでバッファエントリが削除され、接続が維持される
- `test_close_session_releases_send_buffers`: 2 セッション構成で、close_session 呼び出し側と WT_CLOSE_SESSION 受信側の両方でバッファが削除され、他セッションのバッファが残る
- `test_connect_stream_reset_releases_session_send_buffers`: CONNECT ストリームのリセットでセッションに属するバッファのみ削除され、他セッションのバッファが残る。同期コールバック (ResetStream / StopSending) のセッション ID が stream_info_ の残存により復元されることも確認する

なお、`get_streams_to_send` は 1 回の呼び出しで全てのデータを返すとは限らない (WT_CLOSE_SESSION 等は他のストリームの書き出し後に返る) ことをテスト実装中に確認し、テストの転送ヘルパーはデータが無くなるまで繰り返す形にした。
