# WebTransport over HTTP/2 が GOAWAY 受信で高レベル層に接続を切らせる

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-goaway-closes-session
- Polished: {YYYY-MM-DD}

## 目的

`H2Session::on_frame_recv_callback` は `NGHTTP2_GOAWAY` を受信すると `closed_ = true` を立てる。高レベル `h2.Client` / `h2.Server` は `is_closed()` を検知して `run()` を抜け TCP を切断する。draft-ietf-webtrans-http2-15 Section 6.13「An endpoint MAY continue using the session and MAY open new WebTransport streams」に反し、graceful shutdown を送ってきた正当なピアに対して既存セッションを打ち切る。`Http2Connection` 側は同じ GOAWAY 受信を graceful (`goaway_received_` フラグで新規ストリームのみ抑止) として扱っており、2 層で解釈が正反対。issue 0155 の h3 版と同型のバグを h2 側にも作る。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` の `NGHTTP2_GOAWAY` 分岐は `h2_session->closed_ = true;` のみ
- `src/webtransport/h2/client.py` の `Client.run` と `src/webtransport/h2/server.py` の `Server._handle_client` は `session.is_closed()` を検知してループを抜ける
- 対照: `src/bindings/http2.cpp` の `Http2Connection::on_frame_recv_callback` は `GoAway` イベントを積み `goaway_received_ = true` を立てるが `closed_` にはしない。RFC 9113 Section 6.8 の graceful shutdown を尊重するコメント付き
- `H2Session` には `goaway_sent_` フィールドがあるが書き込み箇所のみで読み手 0 (ムーブでコピーされるだけ)
- draft-15 Section 6.13 (「GOAWAY 後もセッションを使い続けてよい」)、Section 3.4 (「session terminates when either endpoint closes the CONNECT stream」)
- `H2EventType::SessionDraining` は既に定義されており WT_DRAIN_SESSION 受信で発火する
- h2 側に GOAWAY を扱うテストは存在しない (grep で goaway の出現 0 件)

## 設計方針

- `H2Session::on_frame_recv_callback` の `NGHTTP2_GOAWAY` 分岐で `closed_` を立てるのを止める。代わりに `SessionDraining` イベント (既存の enum を再利用) を全確立中セッションに対して push する
- 新規セッション開始のみを抑止するフラグ (`goaway_received_` を新設。`Http2Connection` と対称) を立てる
- 高レベル `h2.Client` / `h2.Server` は `SESSION_DRAINING` を `on_session_draining` コールバックで通知し、既存セッションの送受信は継続する
- 死にフィールド `goaway_sent_` は削除するか、`goaway_received_` の実装と合わせて意味を持たせる
- issue 0155 (h3 版) と設計を揃える

## 完了条件

- GOAWAY 受信後も既存 WT セッションで送受信が継続できること
- 新規 CONNECT 要求は抑止されること
- `on_session_draining` (仮) コールバックで GOAWAY 受信をアプリに通知できること
- `tests/` に h2 での GOAWAY 受信後継続を検証するテストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
