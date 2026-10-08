# h3 層で WebTransport セッションを終了コードと理由付きで閉じられるようにする

- Created: 2026-09-23
- Completed: 2026-10-09
- Branch: feature/add-h3-close-apis-with-code-and-reason
- Polished: 2026-10-04

## 目的

h3 層を使うアプリが、WebTransport セッションを終了コードと理由付きで終了できるようにする。

draft-ietf-webtrans-http3-16 Section 6 は、アプリが WT_CLOSE_SESSION カプセルで Application Error Code (32 bit) と Application Error Message (UTF-8、1024 バイト上限) を送れると定める。低レベル `webtransport_ext.h3.Session.close_session(session_id, error_code, error_message)` は既にこれを実装しているが、高レベル `webtransport.h3` からは到達できない。

この API が無いと、プロトコル層がセッション終了理由を持っていてもアプリはピアへ伝えられない。moqt-py は MOQT のセッション終了コードと理由をこの経路で伝えることを前提にしており (外部リポジトリの報告であり本リポジトリでは未確認)、h3 層に口が無いため保留になっている。h2 層は `h2.SessionWriter.close_session(error_code, error_message)` で既に達成済みであり、h3 層だけ非対称が残っている。

## 現状

- `src/webtransport/h3/server.py` の `Server` は `stop()` で全接続を閉じるだけで、1 本の WebTransport セッションを終了する API を持たない。低レベル `h3.Session.close_session` を呼ぶ経路がサーバー側に存在しない。`reset_stream` / `close_stream` はデータストリーム (および CONNECT ストリーム) をリセットする API であり、セッション終了を表現しない
- `src/webtransport/h3/client.py` の `Client.close()` は引数を取らず、内部で `self._webtransport_session.close_session(session_id)` を既定値 `(0, "")` で呼んでいる。アプリは終了コードと理由を指定できない。`close_wait_timeout` まで待ってから CONNECTION_CLOSE を送る best-effort 配信の意味論はこのメソッドが担っている
- 低レベル `h3.Session.close_session` は `error_code` と `error_message` を受け取れる。`src/webtransport/webtransport_ext/h3.pyi` の宣言は `close_session(self, session_id: int, error_code: int = 0, error_message: str = "") -> None` である
- h3 層にはアプリ起点の STOP_SENDING 送出 API が無い。現存する `h3_low.EventType.STOP_SENDING` の分岐 (`h3/server.py` の `_process_webtransport_events`、`h3/client.py` の `_process_webtransport_events` と connect() の応答待ち) は nghttp3 がピアへの STOP_SENDING 送出を要求する方向 (逆方向) であり、アプリがストリームを指定して送る API ではない。ピアからの STOP_SENDING 受信を扱う quic イベント分岐は h3 層に存在しない (0251 の対象)。h2 層の `SessionWriter.stop_sending(stream_id, error_code)` に相当するものが h3 層に無い
- `h2` 層は `SessionWriter.close_session(error_code, error_message)` と `stop_sending(stream_id, error_code)` を持ち、セッション終了と送信停止の両方ができる。h3 層だけができない (`http3` 層は WebTransport セッションの概念がなく close_session の比較対象外で、高レベル API に `stop_sending` も持たない)
- 影響: MOQT を h3 トランスポートで動かす実装では、サーバー側のセッション終了で MOQT の終了コードと理由が捨てられる (moqt-py の `tests/test_e2e.py` 相当の suite で終了理由を観測できない)

## 設計方針

- **`h3.Client.close()` に任意引数を追加する**: `close(error_code: int = 0, error_message: str = "") -> None` とし、内部の `close_session` 呼び出しへそのまま渡す。WT_CLOSE_SESSION 送出・CONNECT ストリームのピア終了待ち・CONNECTION_CLOSE 送出という現在の手順と `close_wait_timeout` の扱いは変えない。`h2.Client.close()` は引数を取らないため、この点だけ層間で形が変わる。引数なしの `close()` が従来どおり error code 0 / 空メッセージで動くことを既存テストで維持する
- **`h3.Server.close_session(addr, session_id, error_code=0, error_message="")` を追加する**: 対象クライアントの低レベル `h3.Session.close_session` を呼び、`_send_to` で送出する。CONNECT ストリームの FIN (draft-16 Section 6 の MUST) と `on_session_closed` の通知は、低レベルが積む `SESSION_CLOSED` イベントを `_process_webtransport_events` の既存分岐が処理することで行われる。当該接続は閉じず、同一接続上の他セッションも継続する (h2 層の `SessionWriter.close_session` と同じセッション単位の操作。`h3.Client.close()` が行うピア終了待ちと CONNECTION_CLOSE は行わない。接続全体を終える API は本 issue の対象外であり、quic 層の `Server.close` は 0220 の対象)。引数の形はサーバー側の他の送出系 API (`send_stream_data` / `reset_stream` / `send_datagram` / `open_stream`) に揃えて `addr` を先頭に取る。`session_id` が対象接続の WebTransport session と一致しない場合 (低レベル `Session.get_session_ids()` に無い場合) と、未登録の `addr` の場合は何も送出しない (`send_datagram` と同じ扱い)。他の接続とサーバー自体は動作を続ける
- **STOP_SENDING の送出**: `h3.Client.stop_sending(stream_id, error_code=0)` と `h3.Server.stop_sending(addr, stream_id, error_code=0)` を追加する。エラーコードは `reset_stream` と同じく低レベル `h3.Session.map_send_error_code` でデータストリームを WT_APPLICATION_ERROR レンジへリマップしてから `quic_low.Connection.stop_sending(stream_id, error_code)` を呼ぶ (draft-ietf-webtrans-http3-16 Section 4.4 の MUST。CONNECT ストリームは HTTP/3 エラーコード空間のままで、32bit 範囲外は ValueError)。h2 層の `SessionWriter.stop_sending` と同じ意味 (RFC 9000 Section 19.5) であり、nghttp3 の状態遷移を伴う `reset_stream` (ストリーム破棄と WT_APPLICATION_ERROR へのリマップを nghttp3 に通知する) とは別操作である
- **対象外**: ピアの STOP_SENDING をアプリへ通知する受信側の対応 (0251)、h3 の書き込み側シャットダウン (0251)、`error_message` の 1024 バイト超の黙示的な切り詰め (低レベル層の挙動に従う)
- **変更対象**: `src/webtransport/h3/client.py` / `src/webtransport/h3/server.py`、`skills/webtransport-py/SKILL.md` (h3 節と注意点)、追加 API のテスト (実通信でピア側の終了コードと理由を観測する)
- 0220 (h3 / h2 の `on_stream_end` 追加) と 0251 (h3 のピア STOP_SENDING 対応) は同じ `src/webtransport/h3/{client,server}.py` のイベント分岐と `skills/webtransport-py/SKILL.md` を触るため、実装順によっては rebase が必要になる (0220 / 0251 と同じ申し送り)

## 完了条件

- `h3.Client.close(error_code, error_message)` が指定した Application Error Code と理由を持つ WT_CLOSE_SESSION を送出し、ピア側でその値が観測できる。引数省略時は error code 0 / 空メッセージになり、既存の待機と CONNECTION_CLOSE の手順は変わらない
- `h3.Server.close_session(addr, session_id, error_code, error_message)` が指定した 1 セッションだけを閉じ、同じサーバー上の他のセッション (対象接続上の他セッションを含む) のデータグラム / ストリーム通信が継続する。当該接続は閉じられない。未登録の `addr` と一致しない `session_id` では何も送出せず、例外にもならない。`stop()` を呼ばずに 1 セッションだけを閉じられる
- `h3.Client.stop_sending(stream_id, error_code)` と `h3.Server.stop_sending(addr, stream_id, error_code)` が STOP_SENDING を送出する。エラーコードは WT_APPLICATION_ERROR レンジへリマップされ、STOP_SENDING を受けたピアは RFC 9000 Section 3.5 の MUST により RESET_STREAM を返すため、呼び出し側が受けるピア側の RESET_STREAM とそのエラーコード (リマップの逆変換後のアプリコード) で検証できる。`reset_stream` の既存の挙動 (データストリームの WT_APPLICATION_ERROR へのリマップ) は変わらない
- 追加分のテストを入れる。終了の事実はピア側の `on_session_closed` で、終了コードと理由は低レベル `Session` の `SESSION_CLOSED` イベント (`error_code` / `error_message` を持つ) で確認する。1 セッションだけを閉じたときに同一接続上の他セッションが生存していることも検証する
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- `skills/webtransport-py/SKILL.md` の h3 節と注意点を追加 API に合わせて更新する
- 全テストが通過する

## 解決方法

0278 のリファクタリングで高レベル実装が `src/webtransport/h3/` から内部モジュールへ移ったため、実装先は `src/webtransport/_h3_client.py` / `src/webtransport/_h3_server.py` である。

- `src/webtransport/_h3_client.py` の `Client.close(error_code: int = 0, error_message: str = "")` に任意引数を追加し、低レベル `Session.close_session` へ渡すようにした。引数省略時は従来どおり終了コード 0 / 空メッセージで、`close_wait_timeout` までのピア終了待ちと CONNECTION_CLOSE の手順は変えていない
- 同 `Client.stop_sending(stream_id, error_code=0)` を追加した。データストリームのエラーコードは低レベル `Session.map_send_error_code` で WT_APPLICATION_ERROR レンジへリマップしてから低レベル `Connection.stop_sending` を呼ぶ (draft-ietf-webtrans-http3-16 Section 4.4 の MUST)
- `src/webtransport/_h3_server.py` の `Server.close_session(addr, session_id, error_code=0, error_message="")` を追加した。低レベル `Session.close_session` を呼んで `_send_to` で送出し、接続と同一接続上の他セッションは閉じない。CONNECT ストリームの FIN と `on_session_closed` の通知は低レベルが積む SESSION_CLOSED イベントを既存の `_process_webtransport_events` が処理する。未登録の `addr` と、確立済みセッションに無い `session_id` では何も送出せず例外にもならない
- 同 `Server.stop_sending(addr, stream_id, error_code=0)` を追加した (リマップの扱いはクライアント側と同じ)
- 統一 API への露出: `src/webtransport/server.py` の `Session` 基底に `stop_sending` / `close_session` を追加して `_H3Session` が実装し、`_H2Session` の同名メソッドはそのまま override する。`src/webtransport/client.py` の `Client.close(error_code=0, error_message="")` と `src/webtransport/_h2_client.py` の `Client.close(error_code=0, error_message="")` も引数を受け取るようにした
- テスト: `tests/test_e2e_webtransport_h3_low_level.py` (高レベル Server + 低レベルクライアントで終了コード・理由・単一セッション終了・未登録ターゲットの no-op・STOP_SENDING のリマップを実 UDP 通信で観測)、`tests/test_webtransport_h3_client_close.py` (高レベル Client + 低レベルサーバーピアで WT_CLOSE_SESSION と STOP_SENDING をワイヤ観測)、`tests/test_unified_close_session.py` (統一 API の `Session.close_session` と `Client.close` を HTTP/2 / HTTP/3 で検証)
- `skills/webtransport-py/SKILL.md` の統一 API・h3 節を追加 API に合わせて更新した
