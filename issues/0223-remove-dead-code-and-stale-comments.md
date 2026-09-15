# デッドコードと取り残されたコメントを削除する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/remove-dead-code-and-stale-comments
- Polished: {YYYY-MM-DD}

## 目的

呼び出されない実装と、削除済みの仕様を説明しているコメントが残っている。読む人に「ここで何か制御している」と誤解させるため削除する。closed/0124 で別の死にコードは削除済みであり、本 issue は残りを扱う。

## 現状

呼び出されない公開メソッド:

- `src/webtransport/http2/server.py` の `Server.submit_response` と `Server.send_data` は、リポジトリ全体で呼び出しが無い。サーバーのコールバックは `ResponseWriter` を渡し、`ResponseWriter` 側が同等の機能を持つ。両メソッドは `asyncio.StreamWriter` と `http2_low.Connection` という利用者が入手できない内部型を引数に取り、`SKILL.md` にも記載が無い

未使用の型とメンバ:

- `src/bindings/webtransport_h3.h` の `struct PendingData` と `H3Session::pending_sends_`、`src/bindings/http3.h` の `struct PendingStreamData` と `Http3Connection::pending_sends_` は、宣言とムーブ操作以外に読み書きが無い
- `H2Session` / `H3Session` / `QuicConnection` のムーブコンストラクタとムーブ代入演算子は、いずれも `std::unique_ptr(new ...)` でしか生成されず一度もムーブされない。`H2Session` のムーブは `unconsumed_recv_bytes_` を移動しておらず、ムーブすると HTTP/2 の受信フロー制御の会計が失われる。`H2Session` / `H3Session` は nghttp2 / nghttp3 へ渡した `this` を差し替える API が無いため、ムーブするとコールバックが移動元を指す

重複した include:

- `src/bindings/webtransport_h2.cpp` が `#include "header_convert.h"` を 2 回書いている

取り残されたコメント:

- `src/bindings/http3.h` に、削除済みの `id` 引数を説明する `Http3Connection::goaway` の古い doc コメントブロックが残り、直後に現行の正しいブロックがある
- `src/bindings/webtransport_h3.h` の「QUIC コントロールストリーム ID を設定」というコメントが `H3Session::is_valid_local_uni_stream_id` の直前に置かれ、本来の対象である `H3Session::bind_control_stream` にコメントが無い

## 設計方針

- 削除のみを行う。代替の追加や設計変更は行わない
- `H2Session` などのムーブ操作は `= delete` する。将来ムーブが必要になった場合は、nghttp2 / nghttp3 の `user_data` を差し替える手段が無いことを踏まえて設計し直す
- 公開メソッドの削除は `CODEBASE.md` の「下位互換を維持しないこと」に従う
- コメントは削除し、必要な説明は残す側のブロックに集約する

## 完了条件

- 上記がすべて削除され、`ruff` / `ty` / `pytest` と prek の全フックが通過する
- 削除によって型スタブの生成物が変わった場合は再生成して追跡分を更新する

## 解決方法

- `src/webtransport/http2/server.py` から `Server.submit_response` と `Server.send_data` を削除する
- `src/bindings/webtransport_h3.h` / `http3.h` から `PendingData` / `PendingStreamData` / `pending_sends_` を削除し、ムーブ操作の参照も消す
- `H2Session` / `H3Session` / `QuicConnection` のムーブコンストラクタとムーブ代入演算子を `= delete` する
- `src/bindings/webtransport_h2.cpp` の重複 include を削除する
- `src/bindings/http3.h` と `src/bindings/webtransport_h3.h` の取り残されたコメントブロックを削除する
