# WebTransport over HTTP/3 クライアントに Origin ヘッダー送信機能を追加する

- Created: 2026-08-01
- Completed: 2026-08-01
- Branch: feature/add-h3-client-origin
- Polished: 2026-08-01

## 目的

サーバー側の Origin 検証 (draft-ietf-webtrans-http3-16 Section 3.2 の MUST) の e2e テストには、Origin ヘッダーを送信できるクライアントが必須である。現在の h3 クライアントは Origin を送信できず、h2 クライアントには `origin` 引数が既にあるため、API の対称性の観点からも必要。仕様上、Origin ヘッダーはブラウザクライアントでは MUST、非ブラウザクライアントでは OPTIONAL であり (draft-ietf-webtrans-http3-16 Section 3.2)、本 issue はサーバー側 MUST 検証を発動させるための任意送信機能の追加である。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::connect` は `:method` / `:scheme` / `:authority` / `:path` / `:protocol` のみを送信し、Origin ヘッダーを送信しない
- `src/webtransport/h3/client.py` の `Client` に origin パラメータがない
- h2 クライアントには `src/bindings/webtransport_h2.cpp` の `connect` に `origin` 引数があり、空でなければ `origin` ヘッダーを付与する。`src/webtransport/h2/client.py` の `Client` にも `origin` パラメータがある

## 設計方針

- h2 クライアントと同様の API 形にする
  - 低レベル API: `Session.connect` に `origin` 引数を追加する (`connect(stream_id, url, origin)`。デフォルトは空文字で、空ならヘッダーを付与しない)。`H3Session::connect` の nva に、h2 と同様に小文字の `origin` ヘッダーを追加する
  - 高レベル API: `Client` のコンストラクタに `origin: str = ""` を追加する (h2 と同様に `verify_peer` の直後)。`connect()` 内の低レベル `Session.connect` 呼び出しに origin を渡す
- `src/bindings/webtransport_h3.h` の宣言と、`src/bindings/webtransport_h3.cpp` の `H3Session::connect` 実装・バインディング (`nb::sig`) を更新する。`h3.pyi` は nanobind の stubgen が自動生成するビルド成果物のため手動更新は不要 (make develop で再生成される)
- issue 0005 との依存: 本 issue は 0005 で実装されるサーバー側 Origin 検証 (`allowed_origins`) の e2e テストに必要なクライアント機能を提供する。0007 → 0005 の順に実装し、Origin 送信の e2e 検証は 0005 の実装完了後に行う

## 完了条件

- 低レベル API と高レベル API の両方で Origin ヘッダーを送信できる
- 0005 で実装されるサーバー側 Origin 検証 (`allowed_origins`) を利用したモックなしの e2e テストで、Origin ヘッダーの送信を検証できる。非許可オリジンの接続が 403 で拒否されることの観測が、Origin ヘッダー送信の決定的な検証となる。403 の観測はサーバー側 (サーバーが 403 を送出し SESSION_READY を発行しないこと) で行う。現行の h3 クライアントは非 200 レスポンスをイベント化しないため、クライアント側での 403 観測は対象外 (0005 の完了条件の検証で確認する)

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `H3Session::connect` に `origin` 引数 (デフォルト空文字) を追加し、空でなければ CONNECT リクエストの nva に小文字の `origin` ヘッダーを付与する (draft-ietf-webtrans-http3-16 Section 3.2: 非ブラウザクライアントでは OPTIONAL)
- `src/bindings/webtransport_h3.h` の宣言と、バインディング (`nb::sig`) を更新した。`h3.pyi` は nanobind の stubgen が自動生成するため手動更新は不要
- `src/webtransport/h3/client.py` の `Client` に `origin: str = ""` を追加し (h2 と同様に `verify_peer` の直後)、`connect()` 内の低レベル `Session.connect` 呼び出しに渡す
- `tests/test_e2e_webtransport_h3.py` に `test_client_connect_with_origin` を追加した。origin 付きリクエストで接続が確立できることのスモークテストであり、Origin ヘッダー送信の実質検証 (403 の観測) はサーバー側 Origin 検証の e2e テストで行う (実装後は本スモークテストを削除する)
