# h2 が RFC 9297 Section 3.2 の malformed 要件 (Content-* と 204/205/206) を満たさない

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-capsule-protocol-malformed

## 目的

Capsule Protocol を使うメッセージの malformed 検出を実装し、非適合な応答をセッション確立として受理しないようにする。現状は 204 / 205 / 206 の応答や Content-Length / Content-Type / Transfer-Encoding を含む応答でもセッションを確立し、カプセルを処理してしまう。

## 現状

- RFC 9297 Section 3.2 (refs/ 未収録。正文で確認) は「The Capsule Protocol MUST NOT be used with messages that contain Content-Length, Content-Type, or Transfer-Encoding header fields. Additionally, HTTP status codes 204 (No Content), 205 (Reset Content), and 206 (Partial Content) MUST NOT be sent on responses that use the Capsule Protocol. A receiver that observes a violation of these requirements MUST treat the HTTP message as malformed.」と定める
- RFC 9113 Section 8.1.1 は「Malformed requests or responses that are detected MUST be treated as a stream error (Section 5.4.2) of type PROTOCOL_ERROR.」「Clients MUST NOT accept a malformed response.」と定める
- `src/bindings/webtransport_h2.cpp` の `H2Session::on_frame_recv_callback` は応答の `:status` を `if (!value.empty() && value[0] == '2') { is_success = true; }` と判定するだけで、204 / 205 / 206 を区別しない。Content-Length / Content-Type / Transfer-Encoding を検査する箇所は `src/` に存在しない
- `src/webtransport/h2/client.py` は「bindings は 2xx 全般 (先頭文字が '2') を確立とみなすため、2xx 非 200 (201 等) でも SESSION_READY が発火する」と docstring に明記している
- サーバー側の `H2Session::reject_session` (`src/webtransport/h2/server.py` の `Server.on_session_request` の戻り値) は 200〜599 を受け付けるため、204 のような Content を持たない 2xx を「拒否」として送れてしまう。`tests/test_webtransport_h2_recv_flow_control.py` は 204 を使う経路を既に利用しており、クライアント側はそれを確立として扱う
- 影響: 非適合なサーバー (または低レベル API の 2xx 誤用) に対してカプセルを処理し、セッションを確立してしまう

## 設計方針

- `H2Session::on_frame_recv_callback` の応答ヘッダー走査で `:status` の値そのものを見て、204 / 205 / 206 を確立として扱わない。200 / 201 / 202 / 203 など Content を持ち得る 2xx は従来どおり確立とする
- 同じ走査で Content-Length / Content-Type / Transfer-Encoding のヘッダーを検出し、存在する場合は確立しない
- 検出時の扱いは RFC 9113 Section 8.1.1 に従い、対象ストリームを PROTOCOL_ERROR のストリームエラーとして扱う (RST_STREAM の送出経路が既存にあるかを確認して流用する)
- 高レベルの通知方法を決める。非 2xx の `H2EventType::SessionRejected` と区別できる形 (malformed を表す情報を `SessionRejected` に載せる、または新しいイベント型を足す) にし、`h2.Client.connect` がどの例外を送出するか (`WebTransportConnectError` 派生) を `skills/webtransport-py/SKILL.md` に反映する
- サーバー側の `reject_session` が 204 / 205 / 206 を受け付けないようにするかは、クライアント側の malformed 検出と役割が重複するため、`reject_session` の入力検証として扱う範囲を実装時に決める
- 変更対象: `src/bindings/webtransport_h2.cpp` / `.h`、`src/webtransport/webtransport_ext/h2.pyi`、`src/webtransport/h2/client.py` / `server.py`、`tests/`、`skills/webtransport-py/SKILL.md`

## 完了条件

- 204 / 205 / 206 の応答でセッションが確立しない (SESSION_READY が発火せず、カプセルが処理されない)
- Content-Length / Content-Type / Transfer-Encoding を含む応答でセッションが確立しない
- 200 / 201 / 202 / 203 などは従来どおり確立し、Content-* を持たない通常の応答の挙動が変わらない
- 非適合な応答を受けたときの高レベル API の通知 (イベント / 例外) が SKILL の記述と一致する
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する

## 解決方法
