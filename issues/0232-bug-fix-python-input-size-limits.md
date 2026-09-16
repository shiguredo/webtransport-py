# Python 境界入力のサイズ上限が quic / http2 / http3 のバインディングに無い

- Created: 2026-09-16
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-python-input-size-limits
- Polished: 2026-09-16

## 目的

`shiguredo-python` スキルは「Python ↔ C++ 間のデータ受け渡しでは、入力サイズの上限を明示的に検査すること」を定めている。WebTransport の h2 / h3 バインディングは 1 MiB の上限を持つが、同じリポジトリの quic / http2 / http3 バインディングは `nb::bytes` を無検査で `std::vector` へコピーしている。ローカル呼び出し側が巨大な bytes を渡すと、その分のメモリを確保する。これは Python 呼び出し境界の防御であり、`ngtcp2` / `nghttp2` / `nghttp3` が渡すチャンクの上限を定めるものではない。

## 現状

- `nb::bytes` を入力に取るバインディングは quic / http2 / http3 に 11 箇所あり、いずれもサイズ検査なしで `std::vector<uint8_t>` や `std::vector` のメンバへコピーしている
  - `src/bindings/quic.cpp` の 6 箇所: `QuicConnection::receive` / `send_stream_data` / `send_datagram` / `accept` (引数 `initial_packet`)、`QuicConfig::session_ticket` setter / `early_transport_params` setter
  - `src/bindings/http2.cpp` の 3 箇所: `Http2Connection::receive` / `send_data` / `ping` (引数 `opaque_data`)
  - `src/bindings/http3.cpp` の 2 箇所: `Http3Connection::receive_stream_data` / `send_data`
- 8 MiB の入力を渡すと 11 箇所すべてでサイズを理由にした例外は出ない (実測)。ただし `Http2Connection::ping` は同じ 8 MiB に対して既存の 8 バイト固定検査で `RuntimeError` になる (コピーはその検査より先に走る)
- `src/bindings/webtransport_h2.cpp` は `kMaxPythonInputBytes` (1 MiB) と `check_python_input_size` を持ち 3 経路 (`receive` / `send_stream_data` / `send_datagram`) で、`src/bindings/webtransport_h3.cpp` は同じ実装を持ち 4 経路 (`receive_stream_data` / `receive_datagram` / `send_stream_data` / `send_datagram`) で検査している。定義とエラーメッセージの形式は 2 ファイルで完全に同一である
- 高レベル API はこれらに有界なデータしか渡さない (`quic.Client` / `quic.Server` の受信は `recvfrom(65535)` 由来、`http2` は `read(65535)`、`http3` は `recvfrom(65535)`)。QUIC の `max_udp_payload_size` は設定しておらず RFC 9000 Section 18.2 の既定 65527 になる (実測 `local_max_udp_payload_size` も 65527)。したがって本検査は低レベル API を直接使う利用者と、将来の呼び出し経路追加に対する防御である
- 入力の大きさの由来は経路ごとに異なる。ストリーム系 (`receive_stream_data` / `send_stream_data` / `send_data`) はアプリが任意長を渡せる。DATAGRAM 系はアプリが任意長を渡せるが送信時にピア広告値で破棄され、受信値は 1 パケット (最大 65527) で有界である。`QuicConnection::accept` の `initial_packet` は高レベルの 3 箇所 (`quic.Server` / `h3.Server` / `http3.Server`) が渡す経路そのものである
- テストの先例として `tests/test_webtransport_h2_input_limit.py` (3 経路) と `tests/test_webtransport_h3_input_limit.py` (4 経路) があり、1 MiB / 1 MiB + 1 の境界値の型が確立している

## 設計方針

- 上限値は h2 / h3 と同値の 1 MiB (1048576 バイト) に揃える。層ごとに閾値が食い違うと「どの API が何バイトまで通るか」が利用者から見て分からなくなるため、既存の決定を引き継ぐ。判定は `>` とし 1 MiB ちょうどは受理する
- 検査は `nb::bytes` から `std::vector` へコピーする前に行い、超過は `std::invalid_argument` (Python の `ValueError`) として通知する
- 検査はセッション状態のガード (`closed_` / `enable_datagram` 等による早期 return) より先に置く。後ろに置くと終了済み接続や DATAGRAM 無効時に 1 MiB 超が黙殺され、h2 / h3 の「終了済みでも入力検査が先に走る」契約と食い違う
- 検査の実装は新しい共通ヘッダ (`src/bindings/python_input.h` を想定) に `kMaxPythonInputBytes` と `check_python_input_size` を置き、quic / http2 / http3 の 11 箇所に加えて h2 / h3 の既存 7 箇所もそこへ寄せる。同一実装が 5 ファイルに散るのを避ける。ヘッダ名と関数名は既存実装の名前を引き継ぐ。`header_convert.h` は「nghttp2 / nghttp3 のヘッダー表現への変換ヘルパー」と目的が限定されているため使わない (quic.cpp は `header_convert.h` を include していないため、集約先が別途必要)。h2 側の `static_assert(kMaxPythonInputBytes == H2SessionConfig{}.wt_max_capsule_payload_size)` は h2 固有の担保なので `webtransport_h2.cpp` に残す
- `src/bindings/webtransport_h2.cpp` の `#include "header_convert.h"` が 2 行重複している。共通ヘッダを扱う際に同じファイルへ触れるため、この重複も解消する
- docstring は h2 / h3 の先例に合わせる。低レベル API には「data が 1 MiB 超の場合は ValueError」を追記し、高レベル `quic.Client` / `quic.Server` の送信系にも `Raises: ValueError` を追記する (実際のガードに合わせ、client は「接続済みで」、server は「addr が登録済みで」という条件を付ける。0216 と同じ形)。docstring を変えると追跡対象の型スタブに波及するため、`make develop` で `quic.pyi` / `http2.pyi` / `http3.pyi` を再生成して追跡分を更新する
- `ping` の 8 バイト固定検証は RFC 9113 Section 6.7 のプロトコル要件であり変更しない。1 MiB 超は上限検査で `ValueError`、空以外の 8 バイト以外の値は従来どおり `RuntimeError` になる。0 バイト (既定値) は nghttp2 がゼロ埋め 8 バイトを送る既存挙動のままとする

## 完了条件

- quic / http2 / http3 の 11 箇所すべてに上限検査が入る
- `ping` 以外の 10 箇所で 1 MiB 超の入力が `ValueError` になり、1 MiB ちょうどの入力は受理される
- `ping` で 1 MiB 超の入力が `ValueError` になる (1 MiB ちょうどの受理は 8 バイト固定検査があるため対象外)
- `tests/test_python_input_size_limits.py` を追加し、11 箇所それぞれで拒否側を、`ping` 以外の 10 箇所で受理側を検証する。`accept` の受理側は有効な Initial パケットを 1 MiB まで伸ばした入力を使う (任意の bytes では受理されない)
- `skills/webtransport-py/SKILL.md` の `### QUIC (quic.Connection)` / `### HTTP/2 (http2.Connection)` / `### HTTP/3 (http3.Connection)` と `## Config の主要デフォルト値` に上限を追記する
- docstring を変更したモジュールの型スタブ (`quic.pyi` / `http2.pyi` / `http3.pyi`) を再生成して追跡分を更新する
- `src/bindings/webtransport_h2.cpp` の `#include "header_convert.h"` の重複を解消する
- 全テストが通過する

## 解決方法

- `CHANGES.md` の `## develop` にはエントリを追加しない。`## develop` は現在空で、`CODEBASE.md` に「この指示がなくなるまでは変更履歴を `CHANGES.md` に残さないこと」という指示がある
- 共通ヘッダの新設は `0221-refactor-consolidate-duplicated-code.md` が扱う C++ 側の共通化 (`is_valid_utf8` 等) と同じ受け皿を作る作業であり、`check_python_input_size` は 0221 の対象一覧に含まれていない。どちらを先に実装しても名前空間の取り合いになるため、0221 の着手時には本 issue で作ったヘッダを拡張先として使う
