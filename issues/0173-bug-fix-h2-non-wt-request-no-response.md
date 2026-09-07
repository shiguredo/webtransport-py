# WebTransport over HTTP/2 のサーバーが非 WT リクエストに一切応答せずストリームが滞留する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-non-wt-request-no-response
- Polished: 2026-09-07

## 目的

`H2Session::on_frame_recv_callback` の CONNECT 判定成立分岐 (`:method=CONNECT` + `:protocol=webtransport`) は SessionReady を発火するが、それ以外のリクエストは `pending_headers_.erase` して何も応答しない。`H2Session` に非 WT リクエストへ応答を送出する公開 API も無い。draft-ietf-webtrans-http2-15 Section 3.2 は「If the target resource does not support WebTransport, the server SHOULD reply with status code 405」を求めるが未実装。実験で平文 GET を 3 本送るとサーバーは何も送出せず、全ストリームが応答待ちのまま残る。コネクションレベルの対処 (GOAWAY 等) は本 issue の対象外とし、ストリーム単位の 405 応答のみを扱う。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` の HEADERS 分岐は `is_connect && is_webtransport` の場合のみ処理し、それ以外は `pending_headers_.erase(it)` するだけ
- `H2Session` に非 WT リクエストへ応答を送出する公開 API が無い (`submit_response` / `submit_goaway` は不在。`reset_stream` は WT_RESET_STREAM 送出の WT ストリーム専用であり、HTTP/2 レベルの RST_STREAM 送出 API は無い)
- 実験 (Sans-IO。検証済み): H2Session ペアのサーバーに平文 GET の HEADERS フレーム (HPACK リテラル、`END_STREAM` 付き) を 3 本注入すると、いずれも戻り値は正で受信自体は成功するが、`server.send()` は何も返さない (応答ゼロ)。101 本の想定は既定 `max_concurrent_streams` 100 (`webtransport_h2.h` の `H2SessionConfig`) + 1 である
- draft-15 Section 3.2 「the server SHOULD reply with status code 405 (Section 15.5.6 of HTTP)」
- 既存 `H2Session::reject_session` は 200-599 検証と `nghttp2_submit_response` (データプロバイダなし = `END_STREAM` 付き) のみで、非 WT ストリーム ID を渡しても送出を塞ぐガードは無い。同一コールバック内の webtransport-init 不正経路では既に `reject_session(stream_id, 400)` を呼んでおり、非 WT 応答への流用は機構的に可能である。流用時は `wt_sessions_.erase` が対象不在の no-op になり、`SessionRejected` 等のイベントも発火しない (黙って 405 を送る)
- 対照: `Http2Connection` は `submit_response` / `reset_stream` を持ち、通常の HTTP/2 サーバーとして機能する

## 設計方針

- 非 WT リクエスト検知時 (`is_connect && is_webtransport` 不成立の else 側) に `reject_session(stream_id, 405)` を呼んで自動的に 405 応答 + `END_STREAM` を送出する (draft-15 の SHOULD に準拠)。webtransport-init 不正時の 400 経路と同位置・同形式であり、新規イベント型や新規応答 API は追加しない。`Allow` ヘッダー付与は refs 内に一次資料が無いため行わない
- `reject_session` の doc に非 WT 応答への使用を明記する (WT セッション拒否の意味論と区別するため)。非 WT への 405 では `wt_sessions_.erase` が no-op になりイベントも発火しないことを前提とする
- 変更対象: `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` の else 側と `src/bindings/webtransport_h2.h` の `reject_session` の doc / `tests/test_e2e_webtransport_h2.py` の Sans-IO 拒否テスト / `CHANGES.md` の develop への FIX エントリ

## 完了条件

- 非 WT リクエストに対して `:status` 405 応答が `END_STREAM` 付きで返ること (Sans-IO クライアントが HEADERS + `END_STREAM` を受信することで確認する)
- 応答後に当該ストリームが両ハーフクローズになり滞留しないこと
- `tests/test_e2e_webtransport_h2.py` に非 WT リクエストの拒否テスト (Sans-IO 形式、`:status` と `END_STREAM` の表明付き) を追加すること
- 既存のテスト全 822 件が引き続き通過すること
