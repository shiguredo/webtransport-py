# h3 にアプリケーションプロトコルネゴシエーション (WT-Available-Protocols / WT-Protocol) が無い

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-application-protocol-negotiation

## 目的

draft-ietf-webtrans-http3-16 Section 3.3 のアプリケーションプロトコルネゴシエーションを扱えるようにする。現状は `WT-Available-Protocols` を送る口も `WT-Protocol` を返す口も無く、交渉を要求したクライアントが必須とされる `WT_ALPN_ERROR` での終了もできない。

## 現状

- draft-ietf-webtrans-http3-16 Section 3.3 は、クライアントが CONNECT に `WT-Available-Protocols` を含めてよい (MAY)、サーバーが 2xx 応答に `WT-Protocol` を含めてよい (MAY) とし、「A client that requires application protocol negotiation MUST close the WebTransport session with a WT_ALPN_ERROR error code if the server does not include a WT-Protocol header field, or if it is malformed and therefore ignored, in a successful response.」「If the client receives a WT-Protocol value that was not included in its WT-Available-Protocols list, the client MUST close the WebTransport session with a WT_ALPN_ERROR error code.」と定める
- `src/` に `wt-available-protocols` / `wt-protocol` の文字列は存在しない。`src/webtransport/h3/_error_codes.py` に `WT_ALPN_ERROR` (0x0817b3dd、Section 9.5) も無い
- `src/bindings/webtransport_h3.cpp` の `H3Session::connect` は `connect(stream_id, url, origin="")` の形で `:method` / `:scheme` / `:authority` / `:path` / `:protocol` と Origin だけを組み立てる。任意のヘッダーを載せる口が無い。`H3Session::accept_session` は内部で `nva` を組み立てて `nghttp3_conn_submit_wt_response` に渡すため、応答に `WT-Protocol` を足す口も無い
- `h3.Client` の `on_session_ready(session_id)` は応答ヘッダーを渡さない。低レベル `h3.Event.headers` は SESSION_READY で受信ヘッダーを持つが、高レベルではアプリから見えない
- 影響: サブプロトコル交渉を行うアプリは h3 トランスポートを選べない。交渉を要求する場合の MUST (`WT_ALPN_ERROR` での終了) を満たす手段も無い

## 設計方針

- `h3.Client` に `available_protocols` (優先順のリスト) を追加し、CONNECT の `WT-Available-Protocols` に RFC 8941 の List (String のみ) として載せる。低レベルは `H3Session::connect` にヘッダーを渡す引数を足すか、`H3Session` に設定メソッドを足して `nghttp3_conn_submit_wt_request` の `nva` を組み立てる
- サーバー側は `h3.Server.on_session_request` に渡す情報から `WT-Available-Protocols` を読めるようにし、受理応答に `WT-Protocol` を載せられるようにする (`accept_session` に応答ヘッダーを渡す形へ拡張する)
- クライアント側で応答の `WT-Protocol` を検証し、要求と不一致または欠落なら `WT_ALPN_ERROR` (0x0817b3dd) でセッションを閉じる経路を追加する。検証を必須にするかアプリのオプトインにするかは、Section 3.3 が「交渉を要求するアプリ」を条件にしているため、オプトインを第一候補とする
- `h3.Client.on_session_ready` で `WT-Protocol` の値 (または応答ヘッダー) をアプリへ渡すかを決める。渡さない場合、アプリは交渉結果を観測できない
- 値のパースは RFC 8941 の Item / List に従う。既存の `H2Session::parse_webtransport_init` と同様の自前実装か、共通のパーサを新設するかを決める (両プロトコルで使うなら共通化する)
- 変更対象: `src/bindings/webtransport_h3.cpp` / `.h`、`src/webtransport/webtransport_ext/h3.pyi`、`src/webtransport/h3/client.py` / `server.py`、`src/webtransport/h3/_error_codes.py`、`tests/`、`skills/webtransport-py/SKILL.md`

## 完了条件

- `h3.Client` が指定した `available_protocols` を `WT-Available-Protocols` として CONNECT に載せ、サーバー側のコールバックでその値を読める
- サーバーが `WT-Protocol` を含む 2xx を返した場合に、クライアント側でその値を取得できる
- 要求と一致しない `WT-Protocol`、または `WT-Protocol` の欠落時に `WT_ALPN_ERROR` でセッションを閉じる (オプトインの指定時のみ)。指定しない場合は従来どおり確立する
- RFC 8941 として妥当でない `WT-Available-Protocols` / `WT-Protocol` は「フィールドごと無視」として扱う (Section 3.3 の規定)
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- `skills/webtransport-py/SKILL.md` の h3 節に追加 API を記載する
- 全テストが通過する

## 解決方法
