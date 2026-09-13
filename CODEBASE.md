# CODEBASE.md

- 良い設計、良い実装のためには積極的に破壊的変更をすること
- 下位互換を維持しないこと
- E2E テスト目的に利用できるよう API を充実させること
- nghttp2 / nghttp3 / ngtcp2 をフォークしないこと。依存ライブラリの改修が必要な機能は、ライブラリ側の対応を待つか、バインディング層で吸収できる設計にすること

## C コールバック境界の例外方針

ngtcp2 / nghttp3 / nghttp2 / BoringSSL へ登録する C コールバック (`*_cb` /
`*_callback`) は、C++ の例外を境界の外へ出してはならない。例外が C のフレームを
巻き戻してライブラリ内部を壊し、プロセスが abort するためである。

- すべての C コールバックに `noexcept` を付与する (宣言と定義の両方)。Python を
  呼ばないコールバックは注釈のみの変更になる
- 例外を送出し得る処理 (Python コールバックの呼び出し等) を含むコールバックは、
  外周で `try` / `catch` して握り、ライブラリのエラー値
  (`NGTCP2_ERR_CALLBACK_FAILURE` / `NGHTTP3_ERR_CALLBACK_FAILURE` /
  `NGHTTP2_ERR_CALLBACK_FAILURE`) を返す。`noexcept` を付けたまま例外を外へ出すと
  `std::terminate` になるため、捕捉は必須である
- 現状で Python コールバックを受け取るのは `quic.Config.verify_callback` のみで、
  対応する `QuicConnection::custom_verify_cb` (`src/bindings/quic.cpp`) がこの方針を
  実装している。Python コールバックを追加する場合は同じ形で捕捉すること
- コールバックから Python へ戻る経路 (nanobind) は `nb::lock_self()` 等で
  オブジェクトの排他を取る。コールバック内から同一オブジェクトのメソッドを
  呼ぶとデッドロックし得るため、再入可能な設計にするか呼び出しを禁じる
