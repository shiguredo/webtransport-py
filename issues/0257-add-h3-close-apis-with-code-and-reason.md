# h3 層で WebTransport セッションを終了コードと理由付きで閉じられるようにする

- Created: 2026-09-23
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-close-apis-with-code-and-reason
- Polished: {YYYY-MM-DD}

## 目的

h3 層を使うアプリが、WebTransport セッションを終了コードと理由付きで終了できるようにする。

draft-ietf-webtrans-http3-16 Section 6 は、アプリが WT_CLOSE_SESSION カプセルで Application Error Code (32 bit) と Application Error Message (UTF-8、1024 バイト上限) を送れると定める。低レベル `webtransport_ext.h3.Session.close_session(session_id, error_code, error_message)` は既にこれを実装しているが、高レベル `webtransport.h3` からは到達できない。

この API が無いと、プロトコル層がセッション終了理由を持っていてもアプリはピアへ伝えられない。moqt-py は MOQT のセッション終了コードと理由をこの経路で伝えることを前提にしており、h3 層に口が無いため保留になっている。h2 層は `h2.SessionWriter.close_session(error_code, error_message)` で既に達成済みであり、h3 層だけ非対称が残っている。

## 現状

- `src/webtransport/h3/server.py` の `Server` は `stop()` で全接続を閉じるだけで、1 本の WebTransport セッションを終了する API を持たない。低レベル `h3.Session.close_session` を呼ぶ経路がサーバー側に存在しない。`reset_stream` / `close_stream` はデータストリーム (および CONNECT ストリーム) をリセットする API であり、セッション終了を表現しない
- `src/webtransport/h3/client.py` の `Client.close()` は引数を取らず、内部で `self._webtransport_session.close_session(session_id)` を既定値 `(0, "")` で呼んでいる。アプリは終了コードと理由を指定できない。`close_wait_timeout` まで待ってから CONNECTION_CLOSE を送る best-effort 配信の意味論はこのメソッドが担っている
- 低レベル `h3.Session.close_session` は `error_code` と `error_message` を受け取れる。`src/webtransport/webtransport_ext/h3.pyi` の宣言は `close_session(self, session_id: int, error_code: int = 0, error_message: str = "") -> None` である
- h3 層にはアプリ起点の STOP_SENDING 送出 API が無い。`quic_low.EventType.STOP_SENDING` の分岐 (`h3/server.py` の `_process_webtransport_events`、`h3/client.py` の受信ループ) はピアの STOP_SENDING を低レベル QUIC へ中継するものであり、アプリがストリームを指定して送る API ではない。h2 層の `SessionWriter.stop_sending(stream_id, error_code)` に相当するものが h3 層に無い
- `http3` 層と `h2` 層は `SessionWriter.close_session` と `stop_sending` を持ち、層を選んでも同じ操作ができる。h3 層だけができない
- 影響: MOQT を h3 トランスポートで動かす実装では、サーバー側のセッション終了で MOQT の終了コードと理由が捨てられる (moqt-py の `tests/test_e2e.py` 相当の suite で終了理由を観測できない)

## 設計方針

- **`h3.Client.close()` に任意引数を追加する**: `close(error_code: int = 0, error_message: str = "") -> None` とし、内部の `close_session` 呼び出しへそのまま渡す。WT_CLOSE_SESSION 送出・CONNECT ストリームのピア終了待ち・CONNECTION_CLOSE 送出という現在の手順と `close_wait_timeout` の扱いは変えない。`h2.Client.close()` は引数を取らないため、この点だけ層間で形が変わる。引数なしの `close()` が従来どおり error code 0 / 空メッセージで動くことを既存テストで維持する
- **`h3.Server.close_session(addr, session_id, error_code=0, error_message="")` を追加する**: 対象クライアントの低レベル `h3.Session.close_session` を呼び、`_send_to` で送出し、`close_wait_timeout` (h3.Client と同じ既定 3.0 秒) を上限にピアの CONNECT ストリーム終了を待ってから当該接続の CONNECTION_CLOSE を送る。引数の形はサーバー側の他の送出系 API (`send_stream_data` / `reset_stream` / `send_datagram` / `open_stream`) に揃えて `addr` を先頭に取る。`session_id` が対象接続の WebTransport session と一致しない場合と、未登録の `addr` の場合は何も送出しない (`send_datagram` と同じ扱い)。他の接続とサーバー自体は動作を続ける
- **`close_wait_timeout` を `h3.Server.__init__` に追加する**: h3.Client と同じ既定値 3.0 秒とし、0 以下なら待機しない。既存呼び出しはキーワード引数の追加のみで影響を受けない
- **STOP_SENDING の送出**: `quic_low.Connection.stop_sending(stream_id, error_code)` をそのまま呼ぶ `h3.Client.stop_sending(stream_id, error_code=0)` と `h3.Server.stop_sending(addr, stream_id, error_code=0)` を追加する。h2 層の `SessionWriter.stop_sending` と同じ意味 (RFC 9000 Section 19.5) であり、nghttp3 の状態遷移を伴う `reset_stream` (CONNECT 以外は WT_APPLICATION_ERROR へリマップする) とは別操作である。エラーコードのリマップは行わない
- **対象外**: ピアの STOP_SENDING をアプリへ通知する受信側の対応 (0251)、h3 の書き込み側シャットダウン (0251)、`error_message` の 1024 バイト超の黙示的な切り詰め (低レベル層の挙動に従う)
- **変更対象**: `src/webtransport/h3/client.py` / `src/webtransport/h3/server.py`、`skills/webtransport-py/SKILL.md` (h3 節と注意点)、追加 API のテスト (実通信でピア側の終了コードと理由を観測する)

## 完了条件

- `h3.Client.close(error_code, error_message)` が指定した Application Error Code と理由を持つ WT_CLOSE_SESSION を送出し、ピア側でその値が観測できる。引数省略時は error code 0 / 空メッセージになり、既存の待機と CONNECTION_CLOSE の手順は変わらない
- `h3.Server.close_session(addr, session_id, error_code, error_message)` が指定した 1 セッションだけを閉じ、同じサーバー上の他のセッションのデータグラム / ストリーム通信が継続する。未登録の `addr` と一致しない `session_id` では何も送出せず、例外にもならない。`stop()` を呼ばずに 1 セッションだけを閉じられる
- `h3.Client.stop_sending(stream_id, error_code)` と `h3.Server.stop_sending(addr, stream_id, error_code)` が STOP_SENDING を送出する。STOP_SENDING を受けたピアは RFC 9000 Section 3.5 の MUST により RESET_STREAM を返すため、ピア側の RESET_STREAM とそのエラーコードで検証できる。`reset_stream` の既存の挙動 (データストリームの WT_APPLICATION_ERROR へのリマップ) は変わらない
- 追加分のテストを入れる。終了コードと理由はピア側の `on_session_closed` と、理由を観測できる API (低レベル `Session` のイベント) で確認する。1 セッションだけを閉じたときに他セッションが生存していることも検証する
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- `skills/webtransport-py/SKILL.md` の h3 節と注意点を追加 API に合わせて更新する
- 全テストが通過する

## 解決方法
