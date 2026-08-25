# h3.Client.connect() が 2xx 応答を待たずに成功を返す問題を修正する

- Created: 2026-08-18
- Completed: 2026-08-25
- Branch: feature/fix-h3-client-connect-2xx
- Polished: 2026-08-24

## 目的

draft-ietf-webtrans-http3-16 Section 3.2「From the client's perspective, a WebTransport session is established when the client receives a 2xx response」に反し、`h3.Client.connect()` が 2xx 応答を待たずに True を返す問題を修正する。現在は 403 拒否でも connect() が True を返し、アプリが拒否を検知する手段が API に存在しない。

## 現状

- `src/webtransport/h3/client.py` の `Client.connect` は CONNECT 送出直後に `_connected = True; return True` する (呼び出し時に応答を待つループがない。応答の処理は run() のイベントループに委ねられる)
- サーバーが非 2xx で拒否しても (a) connect() は True、(b) SessionClosed も発火しない (`src/bindings/webtransport_h3.cpp` の end_headers_cb の非 2xx 分岐は session_ids_ からの削除のみで、イベントを push しない)、 (c) 以後の send_datagram は黙って無視され、open_stream は -1 を返す
- `tests/test_e2e_webtransport_h3.py` の Origin 検証テストが「拒否されても connected is True」を設計ピンとして固定しており、仕様違反の挙動をテストが固定している
- 1xx (103 等) 応答も同じ非 2xx 分岐に入り、セッション ID を削除して SessionClosed を発火しない (`test_client_response_103_session_removed` がピン留め)
- 低レベル H3 の EventType に拒否通知はない。H2 側は SESSION_REJECTED (status_code 付き) を実装済み (closed 0133)。H2 の高レベル `Client.connect` は SESSION_REJECTED を消費して False を返す (closed 0111) 実装になっている
- H3 側の SESSION_READY は 200 応答のみで発火する (2xx 非 200 は SESSION_READY こそ発火しないものの、セッションは確立扱いとして残る)

## 設計方針

- **bindings (`src/bindings/webtransport_h3.cpp`)**: end_headers_cb で 2xx 全般をセッション確立として扱い、SESSION_READY を 2xx 全般で発火させる (open issue 0104 の H2 側変更と同種。0104 の記述「H3 と非対称になる」は本 issue の実装後には解消される)。非 2xx では SessionRejected イベント (status_code フィールド付き) を push する。イベント名・構造は closed 0133 の H2 実装と揃える。現行の非 2xx 分岐の「session_ids_ からの削除」「SessionClosed 非発火」は維持する
- **高レベル `src/webtransport/h3/client.py`**: connect() が 2xx 応答 (SessionReady または同等の確立シグナル) を受信するまで待機し、True を返す。非 2xx (SessionRejected) を受信した場合は False を返す (closed 0111 の H2 側と同型)。待機中は既存の connect() にある HANDSHAKE / SETTINGS 待ちと同様の receive ループを流用する。応答待ちの間に接続が終わった場合も False を返す
- **拒否の通知**: 高レベル API では connect() の False のみで表現する (closed 0111 の H2 側と同型。コールバックは追加しない)。SessionRejected イベントを高レベルが配信しないのは h3_low (bindings) を直接使う利用形態だけが観測できる、という位置づけにする
- **1xx 中間応答**: 現行の H3 の非 2xx 分岐に 1xx も入り、セッション ID 削除・SessionClosed 非発火の意味論である。本修正では 1xx も SessionRejected (status_code = 1xx の値) として扱い、connect() は False を返す (セッションは確立していない)。なお H2 側 (0133) は 1xx を SessionRejected の対象から除外しており (nghttp2 が中間応答として扱うため)、H3 / H2 で 1xx の扱いが異なることを実装時に考慮する
- 変更対象: `src/bindings/webtransport_h3.cpp` (+ h) / `src/webtransport/h3/client.py` / テスト (test_e2e_webtransport_h3.py の Origin ピン留めの更新、test_webtransport_h3_reject_session.py のピン留め更新) / CHANGES.md (## develop への [FIX])

## 完了条件

- 2xx 応答で connect() が True を返し、セッションが確立として扱われる
- 非 2xx 応答で connect() が False を返す (拒否がアプリへ通知される)
- 拒否後の send_datagram (黙って無視) / open_stream (-1) の API 契約が維持される
- 201 等の 2xx 非 200 でもセッション確立として扱われ、SESSION_READY が発火する (0104 と対の契約)
- CHANGES.md の `## develop` に [FIX] エントリを追加する
- テストが追加・更新され、全テストが通る

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `end_headers_cb` で、クライアント側の応答処理を (a) 2xx 全般 (先頭文字が '2') で SESSION_READY を発火 (200 のみだったため 201 等で高レベル connect がハングした)、(b) 非 2xx (1xx を含む) で SessionRejected イベント (status_code フィールド付き。h2 側の SessionRejected と同じ構造) を push するように変更した。session_ids_ の削除・SessionClosed 非発火 (既存の意味論) は維持
- `src/webtransport/h3/client.py` の `Client.connect` が 2xx 応答 (SessionReady) を受けるまで待ち、True を返すようにした。SessionRejected (または接続終了) で False を返す (0111 の h2 側と同型)。待機中は HANDSHAKE / SETTINGS 待ちと同様の receive ループを流用し、QUIC イベントの変換 (STREAM_DATA / DATAGRAM / STREAM_RESET / CONNECTION_CLOSED) も行う
- SESSION_READY は未配信バッファへ引き継ぎ、run() のイベントループで on_session_ready を発火させる (0110 と同じ方式。登録順序に依存しない)。close() でバッファをクリア
- テスト: 403 拒否で connect() = False (origin テスト・transport params テストの更新)、SessionRejected の status_code 検証 (403 / 1xx)、201 で SESSION_READY 発火のピン更新
