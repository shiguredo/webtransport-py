# free-threading (3.14t) で同一の QUIC / HTTP 接続を複数スレッドから触ると abort する

- Created: 2026-09-06
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-free-threading-thread-safety
- Polished: 2026-09-07

## 目的

`CMakeLists.txt` で `FREE_THREADED` を宣言しているにもかかわらず、C++ バインディングにはオブジェクト単位の排他 (`nb::lock_self` / `std::mutex` 等) が 1 箇所も無い。3.14t の GIL 無効環境で同一ハンドルへの同時アクセスを行うと ngtcp2 内部の assert で abort する。実験で再現済み。free-threading を宣言している以上、この経路を排他で安全にする (文書化による回避は選ばない)。

## 現状

- `CMakeLists.txt` の `nanobind_add_module(webtransport_ext NB_DOMAIN "webtransport" FREE_THREADED ...)` で free-threading を宣言
- `pyproject.toml` の classifier に `Programming Language :: Python :: Free Threading :: 2 - Beta` を掲載
- CI matrix で 3.14 と 3.14t を並行検証
- `src/bindings/` 配下に `nb::lock_self` / `nb::ft_mutex` / `std::mutex` / `std::lock_guard` の使用は 0 件 (grep 済み)
- `python3.14t` で拡張を import しても `sys._is_gil_enabled()` は False のまま (nanobind の `Py_MOD_GIL_NOT_USED` 宣言。実行で確認済み)
- 実験 (3.14t。検証済み): Sans-IO ペア確立済みの 1 個の `quic.Connection` に対し 2 スレッドから `send()` / `receive()` を 5 秒間叩き続けると、プロセスが終了コード 134 (SIGABRT) になる。観測した assert は実行の interleaving により変動する (例: `conn_update_timestamp` 内の単調時刻 assert)。ngtcp2 の再送関連 assert (`conn->crypto.retry_aead_ctx.native_handle` の否定) も同系の報告がある
- ngtcp2 / nghttp3 / nghttp2 / SSL はいずれも同一ハンドルへの同時アクセスに対してスレッド安全でない
- README / SKILL.md には「Free-Threading 対応」の記載はあるが、同一オブジェクトの並行使用の可否は明記されていない

## 設計方針

- 全公開メソッドに `nb::lock_self()` を付与し、オブジェクト単位の排他を保証する。対象は 5 バインディングクラス (`quic.Connection` / `http3.Connection` / `http2.Connection` / `h3.Session` / `h2.Session`) の全公開メソッド (約 145 箇所の `.def`) であり、一律付与のため取捨選択はしない。宣言 (FREE_THREADED 維持) と挙動を一致させる (shiguredo-python の同期条項と整合)
- 再入デッドロックの解析を必須作業とする。C コールバック (`verify_callback` 等) 経由で Python が同一オブジェクトのメソッドを呼ぶと、保持中のロックとのデッドロックになり得るため、コールバック呼び出し中はロックを保持しない配置にする。解析結果と配置理由をコードコメントに残す
- asyncio 単一スレッド利用時のオーバーヘッド (uncontended lock) は既存テスト群の実行時間で退行確認する。数値目標は設けず、有意な悪化があれば issue に記録して方針を再検討する
- shiguredo-python の「グローバルな可変状態を共有するときは `threading.Lock` 等で同期する」との整合性を CODEBASE.md か README で明記する

## 完了条件

- 3.14t 環境で 5 クラスの代表操作を 2 スレッドから並行して触るテストが abort しないこと
- `tests/test_quic_free_threading.py` を新規作成し、`quic.Connection` の 2 スレッド hammer (確立済みペアに 5 秒間 `send()` / `receive()`) 回帰テストと、他 4 クラスの代表操作の並行テストを追加すること (`--timeout=30` で timeout 内に完了)
- 既存のテスト全 834 件が引き続き通過すること
