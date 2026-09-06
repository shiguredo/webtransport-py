# WebTransport over HTTP/2 のサーバーが WebTransport 以外のリクエストに一切応答せずストリームが滞留する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-non-wt-request-no-response
- Polished: {YYYY-MM-DD}

## 目的

`H2Session::on_frame_recv_callback` の CONNECT 判定成立分岐 (`:method=CONNECT` + `:protocol=webtransport`) は SessionReady を発火するが、それ以外のリクエストは `pending_headers_.erase` して何も応答しない。`H2Session` に RST_STREAM / 応答 / GOAWAY を送出する高レベル API も無い。draft-ietf-webtrans-http2-15 Section 3.2 は「If the target resource does not support WebTransport, the server SHOULD reply with status code 405」を求めるが未実装。実験で 101 本の GET を送るとサーバーは応答も RST も返さず、クライアントは全ストリームで永久に待つ (半開きのストリームがピア側に滞留)。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` の HEADERS 分岐は `is_connect && is_webtransport` の場合のみ処理し、それ以外は `pending_headers_.erase(it)` するだけ
- `H2Session` に RST_STREAM / `submit_response` / `submit_goaway` の公開 API が無い (grep 済み)
- 実験: `http2.Connection` クライアントから 101 本の GET を送ると応答も RST も無く、クライアントは永久に待つ
- draft-15 Section 3.2 「the server SHOULD reply with status code 405 (Section 15.5.6 of HTTP)」
- 既存 `H2Session::reject_session` は WebTransport CONNECT の拒否 (200-599) 専用で通常リクエストの拒否には流用できない
- 対照: `Http2Connection` は `submit_response` / `reset_stream` を持ち、通常の HTTP/2 サーバーとして機能する

## 設計方針

- `H2Session` に「WebTransport 以外のリクエストを拒否する」ハンドラを追加する。以下の 2 案から選ぶ:
  - 案 A: 非 WT リクエストを検知した時点で自動的に 405 応答 + END_STREAM を送出する (draft-15 の SHOULD に準拠)
  - 案 B: 非 WT リクエストのイベント (`H2EventType::NonWebTransportRequest` 相当) を発火し、アプリが `H2Session.submit_response` / `reset_stream` (新規追加) で応答を選べるようにする
- CODEBASE.md「E2E テスト目的に利用できるよう API を充実させること」に整合させるため案 B が望ましい (アプリが 405 / 404 / 501 を選べる)
- 405 応答時は `Allow: CONNECT` ヘッダーを付けて WT-only エンドポイントであることを示す (HTTP RFC 準拠)
- `H2Session::reject_session` の 200-599 制約とは別経路で扱う (WT 判定成立前の応答)

## 完了条件

- 非 WebTransport リクエストに対して 405 応答が返ること
- ストリームが半開きのまま滞留しないこと (END_STREAM 付き応答で両ハーフクローズ)
- `tests/test_e2e_webtransport_h2.py` に非 WT リクエストの拒否テストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
