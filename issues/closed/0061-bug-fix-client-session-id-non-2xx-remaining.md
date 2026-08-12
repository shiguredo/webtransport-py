# クライアントが非 2xx 応答を受信しても session_ids_ からセッション ID が削除されない

- Created: 2026-08-11
- Completed: 2026-08-12
- Branch: feature/fix-client-session-id-non-2xx-remaining
- Polished: 2026-08-12

## 目的

サーバーが CONNECT リクエストを拒否 (403 等の非 2xx 応答) した場合、クライアントの `session_ids_` にセッション ID が残り続け、拒否されたセッション ID 宛の `send_datagram` がデータグラムを送出してしまう問題を修正する。draft-ietf-webtrans-http3-16 Section 3.2 は「クライアントの視点では、2xx 応答を受信したときにセッションが確立される」と定めており、非 2xx 応答を受信したセッションは確立されなかったものである。楽観的送信 (Section 4。応答を受信していなくても送信してよいという許容) の枠を超えて、拒否後の送信が継続する経路を塞ぐ。

## 現状

- `src/bindings/webtransport_h3.cpp` の `connect` は CONNECT リクエスト送信時に `session_ids_` へセッション ID を挿入する
- `end_headers_cb` のクライアント側分岐は `:status` が 200 のときのみ SESSION_READY を発火するが、非 2xx 応答 (拒否) を受信したときに `session_ids_` から削除する処理が存在しない
- nghttp3 は非 2xx 応答を受信した CONNECT ストリームを reset する (abort_stream) ため `end_stream` コールバックが発火せず、既存の FIN 経路でも削除されない。結果として、サーバーが `reject_session` で 403 を返しても、クライアントの `get_session_ids()` は拒否された ID を返し続け、その ID 宛の `send_datagram` はデータグラムを送出する (Sans-IO 構成で実測確認済み)

## 設計方針

- `end_headers_cb` のクライアント側分岐で、受信した `:status` が 2xx 以外の場合に `session_ids_` からセッション ID を削除する。削除条件は「200 以外」ではなく「2xx 以外」とする: nghttp3 は 2xx 全般をセッション確立として扱う (`status_code / 100 == 2` による confirm。201 等の 2xx 非 200 応答でもセッションが確定する) ため、200 以外で削除すると有効なセッションを誤って削除する。1xx 中間応答は nghttp3 が非 2xx としてストリームを reset する (1xx のヘッダー処理で status_code が -1 に戻り、`-1 / 100 == 2` が成立しないため abort_stream に至る。実測確認済み) ため、2xx 以外の条件で削除しても nghttp3 の状態と矛盾しない
- SESSION_READY の発火条件は変更しない (`:status == "200"` のまま。2xx 非 200 応答で SESSION_READY が発火しないのは既存の制約として残す)
- SessionClosed イベントは発火しない (黙って削除する)。非 2xx 応答ではセッションは一度も確立されていない (draft-ietf-webtrans-http3-16 Section 3.2) ため、SessionClosed (セッション終了の通知) の意味論が合わない。削除後は `close_stream` の CONNECT ストリーム判定 (`session_ids_` のメンバーシップ確認) が成立しなくなるため二重発火の経路も残らない。高レベル API には拒否をアプリへ通知する手段が現状存在しない (イベント化しない) が、これは既知の制約として残す (本 issue のスコープ外)
- 拒否を学習する前に `send_datagram` で `pending_datagrams_` に積まれたデータグラムは、削除後に `get_datagrams_to_send` でそのまま送出される (0057 と同じ扱い。禁止対象は「新しいデータグラム」であり、既にキュー済みの送出はスコープ外)
- 拒否前に楽観的に `open_stream` したデータストリームへの `send_stream_data` が拒否後も継続する点は本 issue のスコープ外とする (受信側の扱いは draft-ietf-webtrans-http3-16 Section 3.2 の「受理されなければ破棄」の方向に従う)
- WebTransport over HTTP/2 (`src/bindings/webtransport_h2.cpp`) にも同種の残留経路が存在する (クライアントが非 2xx 応答を受信しても `wt_sessions_` のエントリが残る) が、本 issue は HTTP/3 のみを対象とする
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` (`end_headers_cb` のクライアント側分岐)、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- サーバーが非 2xx (例: 403) で拒否した場合、クライアントの `session_ids_` からセッション ID が削除される
- 拒否されたセッション ID 宛の `send_datagram` がデータグラムを送出しない
- 拒否されたセッションに対して SessionClosed イベントが発火しない (黙って削除)
- 通常のセッション確立 (200 応答) と 2xx 非 200 応答 (201 等) のセッションは誤って削除されない (2xx 非 200 は Sans-IO 構成で `reject_session(stream_id, 201)` により 201 応答を生成し (accept_session は 200 固定)、FIN を保留して届けて `session_ids_` に残ること・SESSION_READY が発火しないことを確認する。FIN 到着後は既存の FIN 経路で後始末される)
- モックなしの Sans-IO テストで検証できる (サーバーが `reject_session` で 403 を送出し、クライアントが受信した後に `get_session_ids()` / `get_datagrams_to_send()` / イベントを確認する構成)

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `end_headers_cb` クライアント側分岐を変更し、受信した `:status` が 2xx 以外の場合に `session_ids_` からセッション ID を削除した。削除条件は「200 以外」ではなく「2xx 以外」(`status[0] != '2'`) とし、nghttp3 が 2xx 全般をセッション確立として扱うこと (`status_code / 100 == 2` による confirm) と整合させた
- SessionClosed イベントは発火しない (黙って削除)。非 2xx 応答ではセッションは一度も確立されていないため終了通知の意味論が合わない。削除後は `close_stream` の CONNECT ストリーム判定が成立しなくなり二重発火の経路も残らない
- SESSION_READY の発火条件 (`:status == "200"`) は変更していない
- 1xx 中間応答も同じ分岐で削除する。現在の依存 nghttp3 は 1xx 受信時に `status_code` を -1 へ戻して非 2xx として CONNECT ストリームを abort するため、nghttp3 の状態と矛盾しない (nghttp3 が 1xx を中間応答として扱う更新が入った場合は見直しが必要とコメントに明記した)
- `src/bindings/webtransport_h3.h` の `send_datagram` / `close_stream` のドキュメントコメントを更新し、非 2xx 応答受信がセッション ID の削除経路であることと、拒否後の `close_stream` が 1 回目から -1 を返すことを明記した
- `tests/test_webtransport_h3_reject_session.py` を新規追加した (15 件)。非 2xx (403 / 302 / 500) での削除・`send_datagram` 送出抑止・`open_stream` 失敗・SessionClosed 不発火、1xx (103) での削除、2xx 非 200 (201) の保持と FIN 経路での後始末、通常の 200 確立への影響なし、をモックなしの Sans-IO 構成で検証する
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
