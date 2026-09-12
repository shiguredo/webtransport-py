# WebTransport over HTTP/3 の受理前データストリームのバッファリング上限がない

- Created: 2026-09-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-buffering-limits
- Polished: 2026-09-12

## 目的

draft-ietf-webtrans-http3-16 Section 4.6 は、セッション確立前に届いたストリームとデータグラムについて、確立済みセッションに関連付けられるまでバッファしてよい (SHOULD) としたうえで、「To avoid resource exhaustion, endpoints MUST limit the number of buffered streams and datagrams」と定める。上限を超えたストリームは RESET_STREAM または STOP_SENDING を WT_BUFFERED_STREAM_REJECTED で送って閉じ、上限を超えたデータグラムは破棄しなければならない。

h3 バインディングは受理前データグラムを保持しない (サーバーは `end_headers_cb` 前の未知セッション宛を破棄し、クライアントは 2xx 前も `Datagram` イベント化するだけで高レベル層が破棄する)。バッファ数は常に 0 のため、データグラム側の MUST は満たしている。一方、受理前の WebTransport データストリームは nghttp3 内部で無制限にバッファリングされ、上限がない。ストリーム数は QUIC の同時ストリーム数上限 (`quic.Config` の `max_streams_bidi` / `max_streams_uni`、既定 100) で有界だが、1 ストリームあたりの蓄積量が無制限であるため、ピアが大量データを送るだけでメモリを枯渇させられる。nghttp3 1.18.90 の公開 API には受理前バッファの上限設定・量の観測がなく (設定構造体 `nghttp3_settings` に該当項目がない)、`NGHTTP3_ERR_WT_BUFFERED_STREAM_REJECTED` は非公開の `nghttp3_conn_on_wt_stream` が構造的な不整合時のみ返す。このためバインディング層で受理前ストリームの累計を計数し、上限超過時に WT_BUFFERED_STREAM_REJECTED で拒否する。

## 現状

- `H3SessionConfig` に受理前ストリームのバッファ上限がない (`src/bindings/webtransport_h3.h`)
- 受理前 (セッション受理前) の WebTransport データストリームのデータは nghttp3 が `WT_SESSION_BLOCKED` で内部バッファリングし、`recv_wt_data_cb` は受理後にしか呼ばれない (`src/bindings/webtransport_h3.cpp` の `recv_wt_data_cb` のコメント、`_deps/nghttp3/webtransport/source/lib/nghttp3_conn.c` の `nghttp3_stream_buffer_data` 呼び出し)。`nghttp3_conn_read_stream2` の戻り値 (consumed) にはバッファされたペイロードが含まれない。受理前ストリームの初回読み取りでは認識用のストリームタイプ / session ID varint 分だけが consumed として返り、ブロック確立後の呼び出しでは 0 が返る
- `session_ids_` はサーバーでは CONNECT リクエスト受信時 (`end_headers_cb`)、クライアントでは `connect()` 時に挿入される。nghttp3 のセッション確定 (`accept_session` / 2xx 応答) より前に入るため、`session_ids_` のメンバーシップは受理済みの判定に使えない
- クライアントの `connect()` は 2xx 待ちのイベントドレインで `SESSION_READY` / `SESSION_REJECTED` / `SESSION_CLOSED` 以外のイベントを破棄するため、この区間に `StopSending` を push しても QUIC の STOP_SENDING が送出されない (`src/webtransport/h3/client.py`)
- nghttp3 1.18.90 の公開ヘッダー `nghttp3.h` に受理前バッファの上限設定・量の取得 API はない。`NGHTTP3_ERR_WT_BUFFERED_STREAM_REJECTED` / `NGHTTP3_WT_BUFFERED_STREAM_REJECTED` (0x3994BD84) は存在するが、上限に基づく拒否をアプリから起動する公開 API はない
- 公開 API `nghttp3_conn_get_stream_wt_session_id` は WebTransport データストリームの session ID を返す (それ以外は -1)。バインディングから受理前ストリームの識別に使える
- 拒否の送出経路は既存の STOP_SENDING と同型にできる。`stop_sending_cb` が `H3EventType::StopSending` を push し、高レベル層が `quic_connection.stop_sending(stream_id, error_code)` を呼ぶ (`src/webtransport/h3/client.py` / `src/webtransport/h3/server.py`)
- 対照: h2 側は `H2SessionConfig::wt_pre_accept_buffer_limit` (既定 65536 バイト) を持ち、超過時は非 2xx (413) で拒否する。`h2.Config` として公開済み
- 受理前ストリームのデータは `stream_buffers_` (送信待ち) や `pending_sends_` (未使用) には入らない。保持しているのは nghttp3 の内部バッファである

## 設計方針

- `H3SessionConfig` に `wt_pre_accept_buffer_limit: uint64_t = 65536` (バイト) を追加する。受理前 WebTransport データストリーム 1 本あたりの累計受信バイト上限であり、h2 の同名設定と単位・既定値を揃える
- 受理済みセッション ID の集合 (例: `accepted_session_ids_`) を追加する。サーバーは `accept_session` の confirm 成功時、クライアントは 2xx 応答処理で SESSION_READY を push する時点で挿入し、セッション終了時に破棄する。confirm 中に WT_CLOSE_SESSION 受信でセッションが終了した場合 (`session_ids_` から削除済みの場合) は挿入しない。受理前の判定はこの集合で行い、`session_ids_` は使わない
- 受理前ストリームの累計バイトをストリーム単位で計数する。`read_from_nghttp3` (全受信経路が通る) の `nghttp3_conn_read_stream2` の戻り後、戻り値が非負の場合のみ公開 API `nghttp3_conn_get_stream_wt_session_id` で session ID を取得する (負値は接続エラーであり、nghttp3 の契約で以降の API 呼び出しは未定義)。session ID が -1 (WebTransport データストリームでない) の場合は計数も拒否も行わない。session ID が受理済み集合にない場合は `length - consumed` (nghttp3 が今回内部バッファへ取り込んだバイト数) を加算する。カウンタは (session ID, stream_id) の組で保持し、受理確定時にセッション単位でまとめて破棄できるようにする。`consumed` には認識用バイト (ストリームタイプ・session ID varint) が含まれるため、それらは自然に計数対象外になる
- 累計が上限を超えたら、そのストリームを WT_BUFFERED_STREAM_REJECTED (0x3994BD84) で拒否する。`stop_sending_cb` と同形の `H3EventType::StopSending` イベントを push して高レベル層から QUIC の STOP_SENDING を送出させ、あわせて受理前バッファを解放するために `read_stream2` から戻った後に `nghttp3_conn_close_stream` でストリームを閉じる (コールバック内での再入を避ける)
- 拒否した stream_id を集合 (例: `rejected_pre_accept_stream_ids_`) で保持し、`read_from_nghttp3` の先頭で確認して、含まれる stream_id のデータは `nghttp3_conn_read_stream2` を呼ばずに破棄する。`nghttp3_conn_close_stream` 後の再投入は nghttp3 がストリームを再生成してストリームタイプを誤解釈し、glitch レート制限や接続エラーを招くため
- 計数と拒否はサーバー / クライアントの両方で同じ扱いにする (受理前はどちらでも起こり得る)
- カウンタはストリームのクローズ (`stream_close_cb`)・セッションの受理確定 (受理済み集合への挿入時にそのセッションのストリーム分を破棄)・セッション終了時に破棄する。受理確定後は計数しない
- クライアントの `connect()` のイベントドレインでも `STOP_SENDING` (必要なら `RESET_STREAM`) を `run()` と同様に処理し、2xx 待ちの間に届いた受理前ストリームの拒否が破棄されないようにする
- `h3.Config` に公開する (nanobind の `def_rw` と `src/webtransport/h3.pyi` の両方)。高レベル `Client` / `Server` は内部で Config を生成するため、設定は低レベル Config 経由とする (h2 の `wt_pre_accept_buffer_limit` と同じ扱い)
- 変更対象: `src/bindings/webtransport_h3.h` / `src/bindings/webtransport_h3.cpp` / `src/webtransport/h3.pyi` / `src/webtransport/h3/client.py` / `tests/test_webtransport_h3_*.py` / `skills/webtransport-py/SKILL.md` / `CHANGES.md`
- 対象外: nghttp3 のフォーク (CODEBASE.md の「nghttp3 をフォークしないこと」に従う)、受理前データグラム (既に破棄済み)、送信データグラムキュー (`pending_datagrams_`) の上限 (アプリの送信起因のローカル資源保護であり Section 4.6 の対象外)、高レベル API への設定口の追加

## 完了条件

- 受理前ストリームの累計が `h3.Config` の `wt_pre_accept_buffer_limit` を超えた場合に、そのストリームが WT_BUFFERED_STREAM_REJECTED で拒否されること (バインディングが STOP_SENDING イベントを push し、高レベル層が QUIC の STOP_SENDING へ変換して送出すること)。拒否後の同一 stream_id のデータが nghttp3 へ再投入されないこと
- クライアントの `connect()` 待機中に届いた受理前ストリームでも拒否イベントが処理されること。ただし低レベル e2e での検証は、QUIC 層に自側が送出する STOP_SENDING をテストから観測する API がなく、受理前状態を再現する生サーバーの構築が必要なため対象外とし、コードレビューで確認する
- 上限以下 (境界の `累計 == 上限` を含む) の受理前ストリームは従来どおり確定後にアプリへ配信されること。受理確定後のストリームは上限の対象外であること
- 上限値が `h3.Config` から設定できること (`src/webtransport/h3.pyi` に反映されていること)
- 上限超過・境界 (`累計 == 上限` と上限 + 1)・上限 0・上限以下 (分割到着)・拒否後の後続データ破棄・受理確定後の対象外を検証する単体テストを追加すること
- `CHANGES.md` の `## develop` に [FIX] エントリが追加されていること
- 既存のテストがすべて通ること
