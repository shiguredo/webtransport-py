# UDP 系サーバー 3 種の except RuntimeError: continue が証明書パス誤設定を黙殺する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-udp-server-except-runtime-error-swallows-config-error
- Polished: {YYYY-MM-DD}

## 目的

`quic.Server` / `h3.Server` / `http3.Server` の `run()` は「未知アドレスからの非 Initial パケット」を破棄する目的で `except RuntimeError: continue` を使っているが、C++ 側の `QuicConnection::accept` が cert / key ファイル読み込み失敗などの設定エラーでも同じ `RuntimeError("Failed to accept QUIC connection from initial packet")` を投げるため、証明書パスの誤りや権限不足でサーバーが全接続を無言で捨て続ける。ログも出ず、`start()` は成功するため運用時の診断が極めて困難。規約「例外を握りつぶさないこと / 想定外の例外は再 raise すること」違反。

## 現状

- `src/webtransport/quic/server.py` の `Server.run` に `try: connection = self._accept_connection(addr, data); except RuntimeError: continue` (「Initial 以外の未知パケットは破棄する」というコメント付き)
- `src/webtransport/h3/server.py` の `Server.run` と `src/webtransport/http3/server.py` の `Server.run` にも同型の構造がある
- `src/bindings/quic.cpp` の `QuicConnection::accept` の Python バインディングは `throw std::runtime_error("Failed to accept QUIC connection from initial packet");` を返す (成功時のみポインタを返す 1 メッセージに潰れている)
- C++ の失敗原因は複数: (a) cert / key の読み込み失敗 (`create_ssl_ctx` が nullptr)、(b) `initialize_server_from_packet` の TLS 初期化失敗、(c) Initial 以外のパケット (`hd.type != NGTCP2_PKT_INITIAL`)、(d) `ngtcp2_conn_server_new` の失敗、いずれも同じ RuntimeError に落ちる
- 実験: 存在しない certfile を渡して `h3.Server(certfile="/nonexistent/cert.pem", ...)` を起動すると `start()` は成功、全接続を無言で破棄、`run()` は継続 (ログも例外もない)

## 設計方針

- C++ 側の `QuicConnection::accept` のエラー種別を分ける。少なくとも「設定エラー (cert / key の読み込み失敗、SSL_CTX 生成失敗、TLS 初期化失敗)」と「パケット不正 (Initial 以外、不正なヘッダー)」を分離する
  - `std::invalid_argument` (設定不正) と `std::runtime_error` (パケット不正) の使い分け、あるいは戻り値 (Optional + error kind) の 2 案から選ぶ
- Python 側の `Server.run` は「パケット不正」だけを `continue` で捨て、「設定エラー」は再 raise して `run()` を止める (規約整合)
- Server.start / Server.__init__ で証明書ファイルの存在と読み取り可能性を事前検証し、`start()` の時点で `FileNotFoundError` / `PermissionError` を送出する (fail-fast)
- 全経路で `logger` に警告を出し、破棄したパケットの概要 (アドレス・サイズ・先頭バイト) を残す

## 完了条件

- 存在しない certfile を渡して `Server.start` すると即座に例外が上がること
- パケット不正 (Initial 以外・不正ヘッダー) は従来どおり破棄され、`run()` は継続すること
- パケット破棄時にログ (WARNING レベル) が出ること
- `tests/` に設定エラーと破棄経路の分離を検証するテストを追加すること
- 既存のテスト全 822 件が引き続き通過すること
