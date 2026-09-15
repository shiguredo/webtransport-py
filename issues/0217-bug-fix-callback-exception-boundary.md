# C コールバック境界の規約違反を解消する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-callback-exception-boundary
- Polished: {YYYY-MM-DD}

## 目的

`CODEBASE.md` の「C コールバック境界の例外方針」は、C コールバックに `noexcept` を付与し、例外を送出し得る処理を含むコールバックは外周で `try` / `catch` して依存ライブラリのエラー値を返すことを定めている。現状はこの方針から外れている箇所があり、メモリ確保の失敗で `std::terminate` に至る経路が残っている。

## 現状

`noexcept` の欠落:

- `src/bindings/webtransport_h3.cpp` の `wt_data_read_callback` に `noexcept` が無い
- 全バインディングのコールバック定義を機械的に確認したところ、`noexcept` が無いのはこの 1 件のみである

例外捕捉の欠如:

- `src/bindings/quic.cpp` の `QuicConnection::recv_stream_data_cb` は `noexcept` の本体で `std::vector` の確保とイベントキューの追加を行う。確保に失敗すると `noexcept` により `std::terminate` になる
- 同種のコールバックが `src/bindings/quic.cpp` (`stream_open_cb` / `recv_datagram_cb` / `stream_close_cb` / `stream_reset_cb`)、`src/bindings/http2.cpp` (受信系)、`src/bindings/http3.cpp` (`recv_data_cb` / `begin_headers_cb` / `recv_header_cb` / `acked_stream_data_cb`) にある
- `try` を持つのは `src/bindings/quic.cpp` の verify 経路と `src/bindings/webtransport_h2.cpp` の数値パースのみである
- `CODEBASE.md` は「Python コールバックを受け取るのは `quic.Config.verify_callback` のみで、対応する `QuicConnection::custom_verify_cb` がこの方針を実装している」と書いており、方針の適用範囲が「Python を呼ぶ経路」なのか「例外を送出し得る処理全般」なのかが読み取りにくい

## 設計方針

- まず `CODEBASE.md` の方針の適用範囲を確定する。確保失敗などの C++ 例外も捕捉対象に含めるなら、各コールバックを `QuicConnection::custom_verify_cb` と同じ形で `try` / `catch` し、`NGTCP2_ERR_CALLBACK_FAILURE` / `NGHTTP3_ERR_CALLBACK_FAILURE` / `NGHTTP2_ERR_CALLBACK_FAILURE` を返す
- 対象を「Python を呼ぶ経路」に限定するなら、その旨を `CODEBASE.md` に明記し、本 issue は `noexcept` の欠落を直すだけにする
- どちらの判断でも `wt_data_read_callback` の `noexcept` は付与する

## 完了条件

- すべての C コールバックに `noexcept` が付いている
- 方針の適用範囲が `CODEBASE.md` に明記され、実装がそれに一致している
- 捕捉を入れる場合は、捕捉後の戻り値がアプリにどう観測されるかを検証するテストが 1 件以上ある
- 全テストが通過する

## 解決方法

- `src/bindings/webtransport_h3.cpp` の `wt_data_read_callback` に `noexcept` を付与する
- 方針判断の結果に応じて `src/bindings/quic.cpp` / `http2.cpp` / `http3.cpp` の各コールバックへ `try` / `catch` を追加するか、`CODEBASE.md` の記述を限定する
- `CODEBASE.md` の「C コールバック境界の例外方針」を判断結果に合わせて更新する
