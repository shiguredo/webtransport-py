# デッドコードと取り残されたコメントを削除する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/remove-dead-code-and-stale-comments
- Polished: 2026-09-15

## 目的

呼び出されない実装と、削除済みの仕様を説明しているコメントが残っている。読む人に「ここで何か制御している」と誤解させるため削除する。closed/0124 で別の死にコードは削除済みであり、本 issue は残りを扱う。

## 現状

呼び出されない公開メソッド:

- `src/webtransport/http2/server.py` の `Server.submit_response` と `Server.send_data` は、リポジトリ全体で呼び出しが無い。サーバーのコールバックは `ResponseWriter` を渡し、`ResponseWriter` 側が同等の機能を公開 API として持つ。両メソッドは `asyncio.StreamWriter` を引数に取るため高レベルの利用者からは扱いにくく、`SKILL.md` にも記載が無い。`http2.Connection` (`webtransport.http2` から公開) を使う場合は低レベル API を直接呼べるため、`Server` 側の同名メソッドを残す理由が無い

未使用の型とメンバ:

- `src/bindings/webtransport_h3.h` の `struct PendingData` と `H3Session::pending_sends_`、`src/bindings/http3.h` の `struct PendingStreamData` と `Http3Connection::pending_sends_` は、宣言とムーブ操作以外に読み書きが無い
- `H2Session` / `H3Session` / `QuicConnection` のムーブコンストラクタとムーブ代入演算子は、いずれも `std::unique_ptr(new ...)` でしか生成されず一度もムーブされない。`H2Session` のムーブは `unconsumed_recv_bytes_` を移動しておらず、ムーブすると HTTP/2 の受信フロー制御の会計が失われる。`H3Session` は nghttp3 に `user_data` を差し替える API が無いため、ムーブするとコールバックが移動元を指す (`H2Session` は `nghttp2_session_set_user_data` で差し替えられるが、会計が失われる問題は残る)

重複した include:

- `src/bindings/webtransport_h2.cpp` が `#include "header_convert.h"` を 2 回書いている

取り残されたコメント:

- `src/bindings/http3.h` に、孤立した doc コメントブロックが 3 つある。いずれも直後に現行の正しいブロックがあり、二重になっている
  - `Http3Connection::goaway` の直前: 削除済みの `id` 引数を説明するブロック
  - `Http3Connection::close_stream` の直前: 「QUIC ストリーム終了を nghttp3 に通知する」という旧ブロック
  - `Http3Connection::reset_stream` の直前: 「ストリームをリセット」という旧ブロック
- `src/bindings/webtransport_h3.h` の「QUIC コントロールストリーム ID を設定」というコメントが `H3Session::is_valid_local_uni_stream_id` の直前に置かれ、本来の対象である `H3Session::bind_control_stream` にコメントが無い

## 設計方針

- 削除のみを行う。代替の追加や設計変更は行わない
- `H2Session` などのムーブ操作は `= delete` する。将来ムーブが必要になった場合は、`H2Session` の受信フロー制御の会計 (`unconsumed_recv_bytes_`) の移動と、`H3Session` の nghttp3 へ渡した `this` の扱い (差し替える API が無い) を踏まえて設計し直す
- 公開メソッドの削除は `CODEBASE.md` の「下位互換を維持しないこと」に従う
- コメントは削除し、必要な説明は残す側のブロックに集約する

## 完了条件

- 上記がすべて削除され、`make develop` で C++ 拡張を再ビルドしてコンパイルが通り、`ruff` / `ty` / `pytest` と prek の全フックが通過する
- 削除対象は nanobind のバインディング定義に現れないため型スタブの生成物は変わらない。`make develop` の stub 一致検査で確認する
- `CHANGES.md` の `## develop` に `[CHANGE]` が追記されている

## 解決方法

- `src/webtransport/http2/server.py` から `Server.submit_response` と `Server.send_data` を削除する
- `src/bindings/webtransport_h3.h` / `http3.h` から `PendingData` / `PendingStreamData` / `pending_sends_` を削除し、ムーブ操作の参照も消す
- `H2Session` / `H3Session` / `QuicConnection` のムーブコンストラクタとムーブ代入演算子を `= delete` する
- `src/bindings/webtransport_h2.cpp` の重複 include を削除する
- `src/bindings/http3.h` の `Http3Connection::goaway` / `Http3Connection::close_stream` / `Http3Connection::reset_stream` の直前に残る孤立 doc ブロックと、`src/bindings/webtransport_h3.h` の `H3Session::is_valid_local_uni_stream_id` 直前のコメントを削除する (現行のブロックは残し、`H3Session::bind_control_stream` にコメントを移す)
- `CHANGES.md` の `## develop` に、公開メソッドの削除を `[CHANGE]` として追記する (削除された `http2.Server.submit_response` / `http2.Server.send_data` を示す)
