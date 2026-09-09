# WebTransport over HTTP/2 のサーバーが非 WT リクエストに一切応答せずストリームが滞留する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-non-wt-request-no-response
- Polished: 2026-09-09

## 目的

`H2Session::on_frame_recv_callback` の CONNECT 判定成立分岐 (`:method=CONNECT` + `:protocol=webtransport`) は SessionReady を発火するが、それ以外のリクエストは `pending_headers_.erase` して何も応答しない。`H2Session` に非 WT リクエストへ応答を送出する公開 API も無い。そのため平文 GET を送るとサーバーは何も送出せず、ストリームが応答待ちのまま滞留する。WebTransport 専用エンドポイントとして非 WT リクエストを 405 で拒否し、ストリームを終端する。

draft-ietf-webtrans-http2-15 Section 3.2 の 405 SHOULD は「extended CONNECT で `:protocol=webtransport` を受けたが対象リソースが WebTransport 非対応」の場合を指し、本実装では `is_connect && is_webtransport` 分岐から `SessionReady` を発火した後の高レベル `on_session_request` の非 2xx 拒否 (closed issue 0134 で実装済み) が該当する。本 issue が扱う else 側はその補集合であり draft の適用範囲ではないため、405 は仕様準拠ではなく実装ポリシーとして送出する。コネクションレベルの対処 (GOAWAY 等) は本 issue の対象外とし、ストリーム単位の 405 応答のみを扱う。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` の HEADERS 分岐は `is_connect && is_webtransport` の場合のみ処理し、それ以外は `pending_headers_.erase(it)` するだけ
- `H2Session` に非 WT リクエストへ応答を送出する公開 API が無い (`submit_response` / `submit_goaway` は不在。`reset_stream` は WT_RESET_STREAM 送出の WT ストリーム専用であり、HTTP/2 レベルの RST_STREAM 送出 API は無い)
- 実験 (Sans-IO。検証済み): H2Session ペアのサーバーに平文 GET の HEADERS フレーム (HPACK リテラル、`END_STREAM` 付き) を 3 本注入すると、いずれも戻り値は正で受信自体は成功するが、`server.send()` は何も返さない (応答ゼロ)
- 既存 `H2Session::reject_session` は 200-599 検証と `nghttp2_submit_response` (データプロバイダなし = `END_STREAM` 付き) のみで、非 WT ストリーム ID を渡しても送出を塞ぐガードは無い。同一コールバック内の webtransport-init 不正経路では既に `reject_session(stream_id, 400)` を呼んでおり、非 WT 応答への流用は機構的に可能である。流用時は `wt_sessions_.erase` が対象不在の no-op になり、`SessionRejected` 等のイベントも発火しない (黙って 405 を送る)
- `reject_session` は `nghttp2_nv nva[]` に `:status` のみを積むため、405 を送出しても `Allow` ヘッダーが付かない
- draft-15 Section 3.2 の 405 SHOULD は extended CONNECT + `:protocol=webtransport` に対する規定であり、else 側 (非 CONNECT / webtransport 以外) は対象外
- RFC 9110 Section 15.5.6 (draft-15 Section 3.2 が 405 の定義として参照する節) は 405 応答に `Allow` ヘッダーを MUST とする
- 対照: `Http2Connection` は `submit_response` / `reset_stream` を持ち、通常の HTTP/2 サーバーとして機能する

## 設計方針

- 非 WT リクエスト検知時 (`is_connect && is_webtransport` 不成立の else 側) に `reject_session(stream_id, 405)` を呼んで 405 応答 + `END_STREAM` を送出する。新規イベント型や Python 向けの新規応答 API は追加しない
- `reject_session` は `status_code == 405` のとき `Allow: CONNECT` を応答ヘッダーに含める (RFC 9110 Section 15.5.6 の MUST)。`Allow` の値は WebTransport エンドポイントがサポートする唯一のメソッドである `CONNECT` とする。高レベル `on_session_request` が WT セッション拒否で 405 を返す経路にも同じ `Allow` が付くが、同節の MUST に沿う
- `reject_session` の doc に、非 WT 応答への使用と 405 時の `Allow` 付与を明記する (WT セッション拒否の意味論と区別するため)。非 WT への 405 では `wt_sessions_.erase` が no-op になりイベントも発火しないことを前提とする
- 変更対象: `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` の else 側と `H2Session::reject_session` / `src/bindings/webtransport_h2.h` の `reject_session` の doc / `tests/test_webtransport_h2_end_stream.py` と `tests/test_webtransport_h2_reject_session.py` / `CHANGES.md` の develop への FIX エントリ

## 完了条件

- 非 WT リクエストに対して `:status` 405 と `allow: CONNECT` を持つ応答が `END_STREAM` 付きで返ること
- `reject_session(session_id, 405)` を呼んだ場合も応答に `allow: CONNECT` が付くこと
- `tests/test_webtransport_h2_end_stream.py` に、平文 GET をサーバーへ注入して自動 405 応答を検証する Sans-IO テストを追加すること。応答ヘッダーはクライアントに `webtransport.http2.Connection` (Sans-IO) を使い、`submit_request` + `send_data(..., eof=True)` で送ったリクエストの HEADERS イベントで `:status` / `allow` を、続く StreamEnd イベントで `END_STREAM` を表明する (応答を送っていない `h2_low.Session` クライアントへ 405 を渡すと nghttp2 が GOAWAY を返すため使わない)
- `tests/test_webtransport_h2_reject_session.py` に `reject_session(session_id, 405)` の `allow: CONNECT` 表明を追加すること。`h2_low.Session` の `SessionRejected` イベントは `headers` が空のため `allow` を観測できない。クライアントに `webtransport.http2.Connection` を使い WT CONNECT を送り、`reject_session(405)` 後の HEADERS イベントで `allow` を表明する
- 既存のテスト全 976 件が引き続き通過すること
