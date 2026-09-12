# HTTP/3 の close_stream のデフォルト error_code を H3_NO_ERROR に変更する

- Created: 2026-09-03
- Completed: 2026-09-12
- Branch: feature/change-close-stream-default-error
- Polished: {YYYY-MM-DD}

## 目的

`Http3Connection::close_stream` のデフォルト error_code を RFC 9114 Section 8.1 の H3_NO_ERROR (0x0100) に変更し、HTTP/3 のエラーコード空間と整合させる。

## 現状

- **HTTP/3 の close_stream デフォルト error_code が H3_NO_ERROR でない**: `src/bindings/http3.h` の `close_stream` と `src/bindings/http3.cpp` のバインディング定義はデフォルト error_code を 0 にしているが、RFC 9114 Section 8.1 の H3_NO_ERROR は 0x0100 であり、H3 エラーコード空間で 0 は未定義
- 高レベル `src/webtransport/http3/client.py` / `server.py` からの呼び出しはなく、低レベル直接呼び出しのテストは明示的に 0 を渡す (`tests/test_http3_message_ext.py` の `close_stream`、`tests/test_http3_stream_state.py` の `close_stream`) ため、影響はデフォルト省略利用者に限定される

## 設計方針

- C++ 側のデフォルト値を `NGHTTP3_H3_NO_ERROR` マクロの参照に変更する (`src/bindings/http3.h` で nghttp3 ヘッダを include 済み)。pyi は nanobind 生成物のため手編集せず、bindings 側の定義変更から再生成する (0133 の確立規約)
- Python 側に独自定数を持ち込まず、0130 で追加予定の `src/webtransport/http3/constants.py` の `H3_NO_ERROR` を single source とする。0130 未完了の間は C++ マクロのみを用いる
- `reset_stream` (デフォルト 0 のまま) の扱いを確定させる。同時変更するか、対象外とする場合は RFC 9114 Section 4.1 / 4.1.1 の abort・cancel 規定に照らした根拠を明記する
- 0132 と同一ファイル (`src/bindings/http3.cpp`) を変更するため、並行着手する場合は順序調整または rebase 前提とする

## 完了条件

- `close_stream` 省略時の STREAM_END イベントの error_code が 0x0100 で観測されるテストがある
- `reset_stream` の扱いが確定している (同時変更または対象外+根拠明記)
- 既存の全テストが通る

## 関連 issue

- `issues/closed/0123-refactor-http-event-details.md` — 分離元 (close_stream 項目を移管)
- `issues/0130-add-http3-error-code-notification-api.md` — `constants.py` の `H3_NO_ERROR` を single source とするため連携する
- `issues/0132-add-http3-bindings-test-force-close.md` — 同一ファイルを変更するため順序調整

## 解決方法

`http3.Connection.close_stream` の既定 `error_code` を H3_NO_ERROR (0x0100) に変更した。

- `src/bindings/http3.h` の `close_stream` の既定値を `NGHTTP3_H3_NO_ERROR` に変更し、`src/bindings/http3.cpp` のバインディングも `nb::arg("error_code") = NGHTTP3_H3_NO_ERROR` と `nb::sig("... = 0x0100")` に合わせた。型スタブは `make develop` で再生成した
- `src/webtransport/http3/constants.py` に `H3_NO_ERROR` (0x0100) を追加した (issue 0130 の constants.py を single source とする方針に従う)。テストで明示値を与えるため `H3_REQUEST_CANCELLED` (0x010C) も併せて追加した
- **`reset_stream` は既定 0 のまま据え置く**と確定した。根拠を `src/bindings/http3.h` の docstring に明記した:
  - `reset_stream` の `error_code` は QUIC RESET_STREAM に載るアプリケーションエラーコードであり、HTTP/3 のエラーコード空間に限らない。WebTransport データストリームは WT_APPLICATION_ERROR レンジの値をそのまま通す
  - 汎用 API のため「状況に応じた HTTP/3 エラーコード」を既定値にできない。RFC 9114 Section 4.1.1 の cancel / reject を HTTP/3 として表明したい呼び出し側は H3_REQUEST_CANCELLED / H3_REQUEST_REJECTED を明示する
  - 既定の 0 は「アプリケーション固有のエラーなし」を意味する
- テストを 3 本追加した。`test_http3_close_stream_default_error_code` (省略時に STREAM_END の error_code が 0x0100)、`test_http3_close_stream_explicit_error_code` (明示値がそのまま載る)、`test_http3_reset_stream_default_error_code_stays_zero` (reset_stream の既定据え置きのピン)

`uv run pytest tests/ --timeout=30` の 1089 件が全て通る。
