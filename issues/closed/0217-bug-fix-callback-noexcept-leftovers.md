# C コールバックの `noexcept` の付与漏れを解消する

- Created: 2026-09-15
- Completed: 2026-09-20
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
- `SSL_CTX_set_alpn_select_cb` に渡すラムダに `noexcept` が付いている。`ngtcp2_crypto_conn_ref::get_conn` に設定するコールバックは静的メンバー関数 `QuicConnection::conn_ref_get_conn_cb` に集約されており、これに `noexcept` が付いている
- ヘッダーで宣言された `*_cb` / `*_callback` と関数スコープのコールバックのすべてに `noexcept` が付いていることを機械的に確認できる (対象の洗い出し方法をテストに残す)
- 無名ラムダを対象に含める理由と、例外捕捉の追加を本 issue の対象外とする理由が `CODEBASE.md` に明記されている
- 全テストが通過する

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `wt_data_read_callback` に `noexcept` を付与した
- `src/bindings/quic.cpp` の `ngtcp2_crypto_conn_ref::get_conn` に設定していた 4 箇所の同一ラムダを、`noexcept` 付きの静的メンバー関数 `QuicConnection::conn_ref_get_conn_cb` 1 つに集約した (4 箇所が同時に付与漏れになった原因を構造的に解消し、命名規則 `*_cb` の走査対象にもなる)。`SSL_CTX_set_alpn_select_cb` へ渡すラムダには `noexcept` を付与した
- `CODEBASE.md` の「C コールバック境界の例外方針」に次を追記した
  - 対象は名前付き関数に限らず、C のコールバックとして登録する無名ラムダも含む (nanobind の `.def` に渡すラムダは Python へ例外を伝播させるため対象外)
  - 例外の捕捉は Python コールバックを呼ぶコールバックには必須であり、それ以外のコールバックは確保失敗などの C++ 例外を現状捕捉しておらず `noexcept` により `std::terminate` で終了する (受容している残リスク。捕捉の追加は別途の対応)
- `tests/test_bindings_callback_noexcept.py` を追加し、`src/` 配下の C++ ソースを走査して 3 つの観点で検出する
  - `*_cb` / `*_callback` の定義と宣言のすべての出現に `noexcept` があること (名前ごとに 1 出現ではなく全出現を検査する)
  - C のコールバックとして登録する無名ラムダ (依存ライブラリの C API 呼び出しの引数、コールバック構造体のフィールドへの代入) に `noexcept` があること。nanobind と標準ライブラリのラムダは対象外とし、どちらにも分類できないラムダがあれば走査の前提が崩れたとして失敗させる
  - 登録箇所 (`callbacks.* = ...` と nghttp2 のコールバックセッター) が指す関数を定義の走査が取りこぼしていないこと (依存ライブラリが提供する `ngtcp2_crypto_*` 等は自前の定義を持たないため対象外)
  - 自前のコールバック定義が登録箇所から拾えていることの逆方向の検査 (登録行が消えても気付けるようにするため。他のコールバックから委譲して呼ばれる `read_data_callback` は除く)
  - ブロックコメント内は対象外とし、複数行に折り返した宣言・登録は連結して判定する。マクロのトークン連結で名前を組み立てる定義、`#if 0` で無効化した定義、接尾辞を持たない関数ポインタ枠 (`ngtcp2_settings::log_printf` など) は対象外
- `noexcept` を外した修正前の状態で 2 テスト (欠落はコールバック定義 1 件とラムダ 5 箇所) が失敗し、修正後に 3 テストが通過することを確認した
- 全テストが通過する
