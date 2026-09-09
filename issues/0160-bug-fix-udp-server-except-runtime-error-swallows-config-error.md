# UDP 系サーバー 3 種の except RuntimeError: continue が証明書パス誤設定を黙殺する

- Created: 2026-09-06
- Completed: 2026-09-09
- Branch: feature/fix-udp-server-except-runtime-error-swallows-config-error
- Polished: 2026-09-07

## 目的

`quic.Server` / `h3.Server` / `http3.Server` の `run()` は「未知アドレスからの非 Initial パケット」を破棄する目的で `except RuntimeError: continue` を使っているが、C++ 側の `QuicConnection::accept` が cert / key ファイル読み込み失敗などの設定エラーでも同じ `RuntimeError("Failed to accept QUIC connection from initial packet")` を投げるため、証明書パスの誤りや権限不足でサーバーが全接続を無言で捨て続ける。ログも出ず、`start()` は成功するため運用時の診断が極めて困難。規約「例外を握りつぶさないこと / 想定外の例外は再 raise すること」違反。

## 現状

- `src/webtransport/quic/server.py` の `Server.run` に `try: connection = self._accept_connection(addr, data); except RuntimeError: continue` (「Initial 以外の未知パケットは破棄する」というコメント付き)
- `src/webtransport/h3/server.py` の `Server.run` と `src/webtransport/http3/server.py` の `Server.run` にも同型の構造がある
- `src/bindings/quic.cpp` の `QuicConnection::accept` の Python バインディングは `throw std::runtime_error("Failed to accept QUIC connection from initial packet");` を返す (成功時のみポインタを返す 1 メッセージに潰れている)
- C++ の失敗原因は複数: (a) cert / key の読み込み失敗 (`create_ssl_ctx` が nullptr)、(b) `initialize_server_from_packet` の TLS 初期化失敗、(c) Initial 以外のパケット (`hd.type != NGTCP2_PKT_INITIAL`)、(d) `ngtcp2_conn_server_new` の失敗、(e) ヘッダーデコード・パス更新の失敗、いずれも同じ RuntimeError に落ちる
- 実験 (quic Server。検証済み): 存在しない certfile / keyfile で `start()` は成功し、正規クライアントの Initial 1 発を送っても接続は 0 件のまま `run()` は継続し、WARNING ログも例外も出ない

## 設計方針

- C++ 側の `QuicConnection::accept` のエラー種別を分ける。設定不正 (cert / key の読み込み失敗、SSL_CTX 生成失敗、TLS 初期化失敗) は `std::invalid_argument` (Python 側で `ValueError` になる) にし、パケット不正 (Initial 以外、不正ヘッダー、デコード失敗) と実行時失敗 (`ngtcp2_conn_server_new` 失敗、パス更新失敗) は `std::runtime_error` のままとする。戻り値変更案は採らない (例外機構で足りるため)
- Python 側の `Server.run` は `ValueError` (設定エラー) を再 raise して `run()` を止め、`RuntimeError` (パケット不正) のみ `continue` で捨てる (3 Server 均一)
- `Server.start` で証明書ファイルの存在と読み取り可能性を事前検証し、存在しない場合は `FileNotFoundError`、読み取り不可の場合は `PermissionError` を送出する (fail-fast。`__init__` は代入のみのまま変えない)。`start` 検証は第一関門であり、起動後の変化 (削除・権限変更) に対する第二関門として `run()` の再 raise が残る
- 破棄時と再 raise 時の両方で module logger (`__name__` の既存 logger) に英語の WARNING を出す。形式はアドレス・サイズ・先頭バイト (hex) とし、`exc_info` は付けない (破棄は正常系のため)。テストは `caplog` で検証する
- 変更対象は `src/bindings/quic.cpp` (accept の例外分け) と 3 Server の `run()` / `start()` のみとする

## 完了条件

- 存在しない certfile を渡して `Server.start` すると即座に `FileNotFoundError` が上がること (3 Server 共通)
- パケット不正 (Initial 以外・不正ヘッダー) は従来どおり破棄され、`run()` は継続し、WARNING ログが出ること
- `tests/test_server_config_error.py` を新規作成し、3 Server の fail-fast と破棄継続・WARNING を検証すること
- 既存のテスト全 834 件が引き続き通過すること

## 解決方法

- C++ 側の `QuicConnection::accept` で設定不正を `std::invalid_argument` (Python 側で `ValueError`) に分け、パケット不正と実行時失敗は `std::runtime_error` のままにした
- 3 Server の `run()` は `ValueError` を警告後に再 raise して止め、`RuntimeError` のみ警告後に破棄継続する
- 3 Server の `start()` で証明書と鍵の存在と可読性を事前検証し、`FileNotFoundError` と `PermissionError` で即時通知する
- `tests/test_server_config_error.py` に 15 件のテスト (3 Server の fail-fast・破棄継続・再 raise・目録外経路) を追加する
- 全 950 件のテストが通過することと、レビュー 3 周で致命的と重要が 0 件であることを確認した
