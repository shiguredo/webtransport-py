# WebTransport over HTTP/3 クライアントに Origin ヘッダー送信機能を追加する

- Created: 2026-08-01
- Completed: YYYY-MM-DD
- Branch: feature/add-h3-client-origin
- Polished: YYYY-MM-DD

## 目的

サーバー側の Origin 検証 (draft-ietf-webtrans-http3-16 Section 3.2 の MUST) の e2e テストには、Origin ヘッダーを送信できるクライアントが必須である。現在の h3 クライアントは Origin を送信できず、h2 クライアントには `origin` 引数が既にあるため、API の対称性の観点からも必要。

## 現状

- `src/bindings/webtransport_h3.cpp` の `connect` (`H3Session::connect`) は `:method` / `:scheme` / `:authority` / `:path` / `:protocol` のみを送信し、Origin ヘッダーを送信しない
- `src/webtransport/h3/client.py` の `Client` と `connect()` に origin パラメータがない
- h2 クライアントには `src/bindings/webtransport_h2.cpp` の `connect` に `origin` 引数があり、空でなければ `origin` ヘッダーを付与する。`src/webtransport/h2/client.py` の `Client` にも `origin` パラメータがある

## 設計方針

- h2 クライアントと同様の API 形にする
  - 低レベル API: `Session.connect` に `origin` 引数を追加する (空ならヘッダーを付与しない)
  - 高レベル API: `Client` のコンストラクタに `origin` パラメータを追加する
- `src/webtransport/h3.pyi` も更新する

## 完了条件

- 低レベル API と高レベル API の両方で Origin ヘッダーを送信できる
- モックなしの e2e テストで Origin ヘッダー付きの接続が確立できる
- h2 クライアントと同様の API 形を提供する
