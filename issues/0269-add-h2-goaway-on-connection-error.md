# h2 の WebTransport 層が接続エラー検知時に GOAWAY を送らない

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h2-goaway-on-connection-error

## 目的

WebTransport over HTTP/2 で、接続エラーを検知したときに GOAWAY を送出できるようにする。RFC 9113 Section 5.4.1 は「接続エラーに遭遇したエンドポイントは、まず GOAWAY を送る SHOULD」と定めるが、h2 層には GOAWAY の送出経路が無く、接続エラーを検知してもピアへ通知できない。

## 現状

- RFC 9113 Section 5.4.1 は「An endpoint that encounters a connection error SHOULD first send a GOAWAY frame (Section 6.8) with the stream identifier of the last stream that it successfully received from its peer.」と定める
- `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` は `SETTINGS_WT_ENABLED` の値が 1 超なら `H2EventType::Error` (NGHTTP2_PROTOCOL_ERROR) を push し、`h2_session->closed_ = true` を立てるだけで GOAWAY を送出しない。draft-ietf-webtrans-http2-15 Section 3.1 の「Clients MUST treat values greater than "1" as a connection error of type PROTOCOL_ERROR」を検知している箇所である
- `src/bindings/webtransport_h2.cpp` に GOAWAY の送出経路が無い。`nghttp2_submit_goaway` を呼ぶのは `src/bindings/http2.cpp` の `Http2Connection::goaway` のみで、h2 層からは到達できない
- 0124 (closed) が「h2 層に goaway() 自体がない」として死にフィールド (`H2Session::goaway_sent_`) を削除しており、API 不在自体は既知である。ただし接続エラー時にピアへ通知できない点は記録されていない
- 高レベル `on_error` は `WT_FLOW_CONTROL_ERROR` 以外を捨てるため、アプリにも理由が伝わらない (0268 の範囲)
- 影響: ピアは自分が原因で接続が終了したことを学習できず、RFC 9113 の SHOULD を満たさない。接続エラーを検知したのに理由がどこにも残らない

## 設計方針

- `H2Session` に GOAWAY の送出を追加し、ローカル検知の接続エラー (現状は `SETTINGS_WT_ENABLED > 1`) で GOAWAY を submit してから閉じる
- Last-Stream-ID は `nghttp2_submit_goaway` に任せる (自前で算出しない)
- 送信 API として公開するか内部専用にするかは、低レベル `http2.Connection.goaway` (`Http2Connection::goaway`) との対称性と、0220 / 0267 / 0268 の API 追加方針と整合させて決める
- GOAWAY 送出後にピアの応答を待つかは決めない。接続エラーの通知は best-effort であり、待つと停止し得る。既存の `closed_` の扱いと送信ループの終了条件を壊さないことを優先する
- 変更対象: `src/bindings/webtransport_h2.cpp` / `.h`、`src/webtransport/webtransport_ext/h2.pyi` (API として公開する場合)、`tests/`、`skills/webtransport-py/SKILL.md` (公開する場合)

## 完了条件

- `SETTINGS_WT_ENABLED` に 1 超の値を受信した接続で、ピアが GOAWAY を観測する
- GOAWAY の後に接続が閉じ、既存の閉鎖経路 (`closed_` の扱い、後続イベントの処理) が変わらない
- 接続エラー以外の経路 (通常の `close_session` 等) では GOAWAY を送らない
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する

## 解決方法
