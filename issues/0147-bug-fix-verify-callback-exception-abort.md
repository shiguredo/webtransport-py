# QUIC の verify_callback が Python 例外を送出するとプロセスが abort する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-verify-callback-exception-abort
- Polished: {YYYY-MM-DD}

## 目的

`quic.Config.verify_callback` に登録した Python コールバックが例外を送出すると、nanobind の `std::function` ラッパが `nb::python_error` を throw し、それが BoringSSL (`SSL_do_handshake`) と ngtcp2 (`ngtcp2_crypto_read_write_crypto_data` ← `ngtcp2_conn_read_pkt`) の C フレームを巻き戻して `std::terminate` に至る。Python の `except` には到達せずプロセスが終了する。証明書検証コールバックはアプリのバリデーションロジックを実装する場所であり、例外送出は当然想定されるべき経路のため修正する。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::custom_verify_cb` が `self->config_.verify_callback(certificates)` を try / catch なしで呼ぶ
- nanobind の `std::function` (`quic.cpp` の Python バインディング) は Python 例外を `nb::python_error` (C++ 例外) として再送出する
- `CMakeLists.txt` に `-fno-exceptions` は無く、依存 C ライブラリのフレームは巻き戻し情報を持たない
- 実験で `verify_callback` 内で `raise ValueError(...)` を発生させると `libc++abi: terminating due to uncaught exception of type nanobind::abi1::python_error` でプロセス終了。Python 側の `except` には到達しない
- 同じ問題は他の C コールバック経路 (nghttp3 / nghttp2 の各コールバック) でも潜在するが、公開 API から Python コールバックを渡せるのは現状 `verify_callback` のみ

## 設計方針

- `QuicConnection::custom_verify_cb` の外周で `nb::python_error` と `std::exception` を捕捉する
- 例外を捕捉した場合は `ssl_verify_invalid` を返し、例外内容 (メッセージ) を `QuicConnection` の内部状態に保持する
- 保持した例外内容は `receive()` が返る際の `ConnectionClosed` イベント (reason にメッセージを載せる) に変換し、アプリ側で `on_connection_closed` から観測できるようにする
- 将来 nghttp3 / nghttp2 側にも Python コールバックを追加する場合に備え、全 C コールバックの外周で try / catch し `NGTCP2_ERR_CALLBACK_FAILURE` / `NGHTTP3_ERR_CALLBACK_FAILURE` / `NGHTTP2_ERR_CALLBACK_FAILURE` 相当を返す方針を CODEBASE.md か README に明記する
- 全 C コールバックに `noexcept` 指定を付けて意図しない例外の透過を防ぐ

## 完了条件

- `tests/test_quic_error_handling.py` (または新規テスト) で、`verify_callback` が `ValueError` / `RuntimeError` / `BaseException` を送出したケース 3 通りを検証し、いずれもプロセスが継続すること
- `on_connection_closed` コールバックが例外情報を含む reason を受け取れること
- `Client.connect()` が `HandshakeFailedError` を送出して復帰すること
- 既存のテスト全 822 件が引き続き通過すること
