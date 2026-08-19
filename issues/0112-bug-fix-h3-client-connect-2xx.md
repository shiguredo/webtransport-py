# h3.Client.connect() が 2xx 応答を待たずに成功を返す問題を修正する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-client-connect-2xx
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 3.2「From the client's perspective, a WebTransport session is established when the client receives a 2xx response」に反し、`h3.Client.connect()` が 2xx 応答を待たずに True を返す問題を修正する。現在は 403 拒否でも connect() が True を返し、アプリが拒否を検知する手段が API に存在しない。

## 現状

- `src/webtransport/h3/client.py` の `Client.connect` は CONNECT 送出直後に `_connected = True; return True` する
- サーバーが 403 で拒否しても (a) connect() は True、(b) SessionClosed も発火しない (`tests/test_webtransport_h3_reject_session.py` の `test_client_non_2xx_response_no_session_closed_event` がピン留め)、(c) 以後の send_datagram / open_stream は黙って無視される
- `tests/test_e2e_webtransport_h3.py` の Origin 検証テストが「拒否されても connected is True」を設計ピンとして固定しており、仕様違反の挙動をテストが固定している

## 設計方針

- connect() が 2xx 応答の受信まで待つか、拒否をアプリへ通知する手段 (拒否イベントまたは例外) を追加する
- 拒否検知後の API 契約 (send_datagram / open_stream の挙動) を整理する
- テストのピン留めを新しい契約に合わせて更新する

## 完了条件

- connect() が 2xx 受信 (またはその代替の確立条件) を反映した値を返す
- 非 2xx 拒否がアプリへ通知される手段がある
- テストが新しい契約に合わせて更新される
