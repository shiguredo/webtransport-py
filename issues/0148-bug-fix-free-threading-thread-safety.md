# free-threading (3.14t) で同一の QUIC / HTTP 接続を複数スレッドから触ると abort する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-free-threading-thread-safety
- Polished: {YYYY-MM-DD}

## 目的

`CMakeLists.txt` で `FREE_THREADED` を宣言しているにもかかわらず、C++ バインディングにはオブジェクト単位の排他 (`nb::lock_self` / `std::mutex` 等) が 1 箇所も無い。3.14t の GIL 無効環境で同一の `quic.Connection` を 2 スレッドから触ると ngtcp2 内部の assert (`assert(!conn->crypto.retry_aead_ctx.native_handle) ... ngtcp2_conn.c line 14189`) で abort する。実験で再現済み。free-threading を宣言する以上、この経路を安全にするか、条件を明示的に文書化する必要がある。

## 現状

- `CMakeLists.txt` の `nanobind_add_module(webtransport_ext NB_DOMAIN "webtransport" FREE_THREADED ...)` で free-threading を宣言
- `pyproject.toml` の classifier に `Programming Language :: Python :: Free Threading :: 2 - Beta` を掲載
- CI matrix で 3.14 と 3.14t を並行検証
- `src/bindings/` 配下に `nb::lock_self` / `nb::ft_mutex` / `std::mutex` / `std::lock_guard` の使用は 0 件 (grep 済み)
- `python3.14t` で拡張を import しても `sys._is_gil_enabled()` は False のまま (nanobind の `Py_MOD_GIL_NOT_USED` 宣言)
- 実験で 3.14t 環境で 1 個の `quic.Connection` を 2 スレッドから `send()` / `receive()` を叩くと `Assertion failed: (!conn->crypto.retry_aead_ctx.native_handle) ... ngtcp2_conn.c line 14189` で abort
- ngtcp2 / nghttp3 / nghttp2 / SSL はいずれもスレッド安全でない
- README / SKILL.md には「Free-Threading 対応」の記載はあるが、同一オブジェクトの並行使用の可否は明記されていない

## 設計方針

以下の 2 案から選択する。いずれも設計判断を伴うため実装前に方針を確定する。

- 案 A: 全公開メソッドに `nb::lock_self()` を付与し、オブジェクト単位の排他を保証する。asyncio 単一スレッド利用時のオーバーヘッドは軽微 (uncontended lock)
- 案 B: `FREE_THREADED` 宣言を維持し、「同一の Connection / Session を複数スレッドから同時に触らない」という契約を README と SKILL.md に明記する。宣言だけで排他はしない

いずれの案でも、shiguredo-python の「グローバルな可変状態を共有するときは `threading.Lock` 等で同期する」との整合性を CODEBASE.md か README で明記する。

## 完了条件

- 3.14t 環境で 1 個の Connection / Session を 2 スレッドから並行して触るテストが abort しないこと (案 A) または、当該利用が契約外である旨が README / SKILL.md に明記されていること (案 B)
- 案 A の場合、`tests/` に free-threading 並行アクセスの回帰テストを追加すること (`pytest --pytest-timeout` で timeout 内に完了)
- 案 B の場合、README の「Python Free-Threading 対応」節に契約 (「Connection / Session は 1 スレッド専有」等) を追記し、SKILL.md にも反映すること
