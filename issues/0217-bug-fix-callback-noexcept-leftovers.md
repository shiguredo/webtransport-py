# C コールバックの `noexcept` の付与漏れを解消する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-callback-noexcept-leftovers
- Polished: 2026-09-15

## 目的

`CODEBASE.md` の「C コールバック境界の例外方針」は、C コールバックに `noexcept` を付与することを定めている。方針の確定と大半の付与は完了済みの対応で実施されたが、`noexcept` が付いていないコールバックが残っており、そこで例外が外へ出ると `std::terminate` になる。本 issue はその付与漏れの解消と、対象範囲の明文化に限定する。

## 現状

- `src/bindings/webtransport_h3.cpp` の `wt_data_read_callback` に `noexcept` が無い。`H3Session::read_data_callback` へ委譲する関数スコープのコールバックで、`nghttp3_data_reader::read_data` として `nghttp3` に登録される
- ヘッダーで宣言された `*_cb` / `*_callback` とコールバック型フィールドへ代入する関数スコープのコールバックを機械的に確認すると、`noexcept` が無いのはこの 1 件のみである
- 一方 `CODEBASE.md` は対象を「`ngtcp2` / `nghttp3` / `nghttp2` / BoringSSL へ登録する C コールバック」としており、無名ラムダの扱いが書かれていない。`noexcept` が無い無名ラムダは 2 種ある
  - `src/bindings/quic.cpp` の `SSL_CTX_set_alpn_select_cb` に渡すラムダ (BoringSSL へ登録する)
  - `src/bindings/quic.cpp` の `ngtcp2_crypto_conn_ref::get_conn` に設定するラムダ (`ngtcp2_crypto` が参照する関数ポインタ)
- 例外捕捉について: 方針は「例外を送出し得る処理を含むコールバックは外周で `try` / `catch` する」と定めているが、現状の `noexcept` コールバックの本体はコンテナ・文字列の確保を行い得る。捕捉の追加は規模が別であり、本 issue の対象外とする
- 方針の確定と大半の `noexcept` 付与は完了済みの対応 (closed の C コールバック横断対応) で実施されている。本 issue はそこで漏れた分を扱う

## 設計方針

- `wt_data_read_callback` に `noexcept` を付与する
- 無名ラムダも対象に含める。`CODEBASE.md` が対象を「`ngtcp2` / `nghttp3` / `nghttp2` / BoringSSL へ登録する C コールバック」と定めている以上、BoringSSL へ登録するラムダと `ngtcp2_crypto` が参照するラムダを除外する根拠が無い。対象に含める旨と理由を `CODEBASE.md` に明記する
- 例外捕捉の追加は本 issue の対象外とする。捕捉対象は確保失敗などの C++ 例外であり、C ABI 境界へ例外を出さない実装 (数値パースに `std::from_chars` を使うなど) を既に採っている。対象外とする理由を `CODEBASE.md` に明記する
- 戻り値が `void` のコールバック (`src/bindings/quic.cpp` の `rand_cb` / `delete_crypto_aead_ctx_cb` / `delete_crypto_cipher_ctx_cb`) はエラー値を返せないため、捕捉を入れる場合は握る (ログに残す) 扱いとする。ただしこの 3 件は本体で確保を行わないため、`noexcept` の付与のみで足りる

## 完了条件

- `wt_data_read_callback` に `noexcept` が付いている
- `SSL_CTX_set_alpn_select_cb` に渡すラムダと `ngtcp2_crypto_conn_ref::get_conn` に設定するラムダに `noexcept` が付いている
- ヘッダーで宣言された `*_cb` / `*_callback` と関数スコープのコールバックのすべてに `noexcept` が付いていることを機械的に確認できる (対象の洗い出し方法をテストに残す)
- 無名ラムダを対象に含める理由と、例外捕捉の追加を本 issue の対象外とする理由が `CODEBASE.md` に明記されている
- 全テストが通過する

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `wt_data_read_callback` に `noexcept` を付与する
- `src/bindings/quic.cpp` の `SSL_CTX_set_alpn_select_cb` に渡すラムダと `ngtcp2_crypto_conn_ref::get_conn` に設定するラムダに `noexcept` を付与する
- `CODEBASE.md` の「C コールバック境界の例外方針」に、無名ラムダも対象に含めることと、例外捕捉の追加を別途扱う理由を追記する
- `src/bindings/` のコールバック定義を走査して `noexcept` の欠落を検出するテストを追加する (対象はヘッダー宣言とコールバック型フィールドへの代入、およびラムダを含む登録箇所)
