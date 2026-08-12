# クライアントの open_stream が失敗時に無効な stream_id を返す

- Created: 2026-08-11
- Completed: 2026-08-12
- Branch: feature/fix-client-open-stream-failure-handling
- Polished: 2026-08-12

## 目的

高レベル `Client.open_stream` が h3 層の `open_stream` の戻り値 (false) を無視して stream_id をそのまま返すため、セッション終了後に呼ばれた場合に QUIC ストリームだけが開いた無効な stream_id が返り、開いたままのストリーム状態が接続終了まで残る問題を修正する。リセットしても消費されたストリーム ID は戻らない (RFC 9000 Section 2.1 のストリーム ID は単調増加) ため、修正の実効は「開いたまま残るストリーム状態の解放 (RESET_STREAM 送出)」と「無効な stream_id の代わりに -1 を返す」ことである。

## 現状

- `src/webtransport/h3/client.py` の `Client.open_stream` は `self._webtransport_session.open_stream(...)` の戻り値を無視して stream_id をそのまま返す
- セッション終了後 (issue 0060 の修正で h3 層の `open_stream` が false を返すようになった) に呼ばれた場合:
  - QUIC ストリームは開かれる (`quic_connection.open_stream`)
  - h3 層への登録は行われないため、以後 `send_stream_data` は黙って無視される
  - QUIC ストリームはリセットされず、接続終了までストリーム状態が残る (ピアのストリーム数制限 (RFC 9000 Section 4.6 の累積制限。クローズ済みのストリームも含む) を消費する。データもリセットも送出されないため、ピアにはストリームの存在自体が通知されない)
- 対照的に `Server.open_stream` (src/webtransport/h3/server.py) は h3 層の false を受けて QUIC ストリームをリセットし -1 を返す (挙動が非対称)

## 設計方針

- `Client.open_stream` を Server.open_stream と対称化する:
  1. `quic_connection.open_stream` の戻り値が -1 の場合は -1 を返す (Server.open_stream の `stream_id < 0` ガードと同じ。接続クローズ済み等でストリームが開けなかった場合に、開かれていないストリームへ `reset_stream` を呼ばないため)
  2. h3 層の `open_stream` が false の場合、`quic_connection.reset_stream(stream_id, 0)` で QUIC ストリームをリセットして -1 を返す (error_code は 0。Server.open_stream の前例と整合。h3 層への通知は不要: ストリームは `stream_info_` に未登録のため、高レベル `reset_stream` の h3 層側呼び出しは無意味)
- リセット (RESET_STREAM) のワイヤへの送出は、`Client.open_stream` 内で `_send_pending` を呼ばず、run ループの `_send_pending` に委ねる (Server 側と同様。e2e テストはクライアントの run タスク起動が前提になる)
- 修正は「h3 層の `open_stream` が false を返す」すべてのケースに効く: セッション終了後・未確立 (connect 失敗後。`_session_id == -1`)・issue 0061 実装後の非 2xx 拒否後。connect 前は既存ガード (`_quic_connection is None` で -1 返却) が処理済みのため本修正の対象外。完了条件はセッション終了後のケースを主対象とする
- `Client.connect()` にも同種の経路 (connect が false を返した場合に QUIC ストリームをリセットしない) が存在するが、到達条件が `nghttp3_conn_submit_wt_request` の失敗に実質限定され極めて稀なため、本 issue のスコープ外とする
- `Client.open_stream` の docstring を更新する: 0060 で追記された「セッション終了後・未確立の場合は … 無効な stream_id が返り得る (サーバー側の Server.open_stream が -1 を返すのと非対称な既知の挙動)」は修正後は不正確になるため、「失敗した場合は -1 を返す (失敗条件: セッション終了後・未確立・接続クローズ済み)」に書き換える (Server.open_stream の docstring と同水準の記述にする)
- 変更対象は `src/webtransport/h3/client.py` (`open_stream` と docstring)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- セッション終了後の `Client.open_stream` が -1 を返し、QUIC ストリームが開いたまま残らない (RESET_STREAM が送出される)
- 生存セッションの `Client.open_stream` は従来どおり stream_id を返す
- 未確立 (connect 前) の `Client.open_stream` も -1 を返す (既存ガードで修正前から実現済みの挙動の回帰確認。e2e 構成で connect 前に呼び出して確認する)
- モックなしの e2e テストで検証できる (セッション終了の注入はテストから `server._clients` のプライベートアクセスで低レベル `close_session` を呼ぶ等の既存テストパターンを使用し、クライアントの `open_stream` の戻り値 (-1) と、サーバー側の `on_stream_reset` による RESET_STREAM の観測 (WT ヘッダー未受信のため session_id は -1、error_code は 0 で届く) で検証する)

## 解決方法

- `src/webtransport/h3/client.py` の `Client.open_stream` を `Server.open_stream` と対称化した:
  - `quic_connection.open_stream` の戻り値が -1 の場合は -1 を返す (接続クローズ済み等でストリームが開けなかった場合に、開かれていないストリームへ `reset_stream` を呼ばないためのガード)
  - h3 層の `open_stream` が false の場合、`quic_connection.reset_stream(stream_id, 0)` で開いた QUIC ストリームをリセットして -1 を返す (リセットしないとローカルのストリーム状態が接続終了まで open のまま残るため。error_code は Server.open_stream の前例と整合する 0。h3 層への通知は不要: ストリームは `stream_info_` に未登録のため)
- RESET_STREAM のワイヤへの送出は run() の送信ループに委ねる (issue の設計方針どおり。docstring に送出タイミングを明記した)
- `Client.open_stream` の docstring を更新した: 失敗条件 (セッション終了後・非 2xx 拒否後・未確立・接続クローズ済み) と、失敗時に -1 を返し RESET_STREAM で解放すること、送出が run() の送信ループに委ねられることを記載した
- `tests/test_e2e_webtransport_h3.py` に e2e テスト 2 件を追加した: セッション終了後の `open_stream` が -1 を返し、サーバー側の `on_stream_reset` で RESET_STREAM (session_id -1、error_code 0) が観測されること (セッション終了の注入はサーバーの低レベル `close_session` をプライベートアクセスで呼ぶ既存パターン)、connect 前の `open_stream` が -1 を返すこと。生存セッションの正常系は既存テストが担保する
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
