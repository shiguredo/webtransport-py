# QUIC の verify_callback が Python 例外を送出するとプロセスが abort する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-verify-callback-exception-abort
- Polished: 2026-09-07

## 目的

`quic.Config.verify_callback` に登録した Python コールバックが例外を送出すると、nanobind の `std::function` ラッパが `nb::python_error` を throw し、それが BoringSSL (`SSL_do_handshake`) と ngtcp2 の C フレームを巻き戻して `std::terminate` に至る。Python の `except` には到達せずプロセスが終了する。証明書検証コールバックはアプリのバリデーションロジックを実装する場所であり、通常の例外送出は想定されるべき経路のため修正する。割り込み・キャンセル系 (`KeyboardInterrupt` / `SystemExit` / `asyncio.CancelledError`) は再送出して握りつぶさない。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::custom_verify_cb` が関数全体で try / catch なしに `self->config_.verify_callback(certificates)` を呼ぶ
- nanobind の `std::function` (`quic.cpp` の Python バインディング) は Python 例外を `nb::python_error` (C++ 例外) として再送出する
- `CMakeLists.txt` に `-fno-exceptions` は無く、依存 C ライブラリのフレームは巻き戻し情報を持たない
- 実験 (高レベル Server + Client。検証済み): 自己署名証明書 (SAN に localhost) のサーバーに `verify_callback` で `raise ValueError(...)` するクライアントで `connect()` を試行すると、`libc++abi: terminating due to uncaught exception of type nanobind::abi1::python_error` でプロセスが終了コード 134 (SIGABRT) になる。Python 側の `except` には到達しない
- 同じ問題は他の C コールバック経路 (nghttp3 / nghttp2 の各コールバック) でも潜在するが、公開 API から Python コールバックを渡せるのは現状 `verify_callback` のみであり、それらの対応は本 issue の対象外とする

## 設計方針

- `QuicConnection::custom_verify_cb` の関数全体を try / catch で囲む。捕捉対象は `nb::python_error` と `std::exception` および `catch (...)` であり、Python 境界を出る前に全例外を捕捉する
- 捕捉した例外のうち `KeyboardInterrupt` / `SystemExit` / `asyncio.CancelledError` (PyErr 正規化後の種別判定) は再送出 (throw) し、`ssl_verify_invalid` には丸めない (shiguredo-python 規約「想定外の例外は再 raise すること」に従う)。それ以外の例外は `ssl_verify_invalid` を返す
- `ssl_verify_invalid` に丸めた例外は、型名 + メッセージの文字列を `QuicConnection` の新規 `std::optional<std::string>` メンバに保持する (ムーブ対応に含める)。書き込みも読み出しも `receive()` 呼び出しスタック内で完結するため、追加の同期機構は設けない
- 保持した文字列は、ハンドシェイク失敗に伴う低レベル `ConnectionClosed` イベントの `reason` に設定する。英語プレフィックスを付けて shiguredo-python 規約の英語表記に従い、既存の `reason` の切り詰め契約に従う。高レベル `on_connection_closed` (無引数) の変更は行わず、観測は低レベル `next_event()` 経由とする
- 全 C コールバックへの `noexcept` 付与と将来方針の文書化は本 issue の対象外とし、別途 issue 化する

## 完了条件

- `tests/test_quic_error_handling.py` (または新規テスト) で、`verify_callback` が `ValueError` / `RuntimeError` / 独自例外を送出したケース 3 通りを検証し、いずれもプロセスが継続すること
- 低レベル `next_event()` の `ConnectionClosed` イベントの `reason` に例外情報が含まれること
- `Client.connect()` が `False` を返して復帰すること
- 既存のテスト全 834 件が引き続き通過すること
