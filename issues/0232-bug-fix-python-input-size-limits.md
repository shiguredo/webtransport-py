# Python 境界入力のサイズ上限が quic / http2 / http3 のバインディングに無い

- Created: 2026-09-16
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-python-input-size-limits
- Polished: {YYYY-MM-DD}

## 目的

`shiguredo-python` スキルは「Python ↔ C++ 間のデータ受け渡しでは、入力サイズの上限を明示的に検査すること」を定めている。WebTransport の h2 / h3 バインディングは 1 MiB の上限を持つが、同じリポジトリの quic / http2 / http3 バインディングは `nb::bytes` を無検査で `std::vector` へコピーしている。ローカル呼び出し側が巨大な bytes を渡すと、その分のメモリを確保する。これは Python 呼び出し境界の防御であり、`ngtcp2` / `nghttp2` / `nghttp3` が渡すチャンクの上限を定めるものではない。

## 現状

- `src/bindings/quic.cpp` の `QuicConnection::receive` / `send_stream_data` / `send_datagram`、`src/bindings/http2.cpp` の `Http2Connection::receive` / `send_data` / `ping`、`src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` / `send_data` の各バインディングは、`nb::bytes` をそのまま `std::vector<uint8_t>` のコンストラクタへ渡している
- 8 MiB の入力を渡すと 8 経路すべてで例外なく通る (実測)
- `src/bindings/webtransport_h2.cpp` / `webtransport_h3.cpp` には `kMaxPythonInputBytes` (1 MiB) と `check_python_input_size` があり、それぞれの 4 経路・3 経路で検査している。実装とエラーメッセージの形式は 2 ファイルで同一である
- 高レベル API はこれらに有界なデータしか渡さない (`quic.Client` / `quic.Server` の受信は `_socket.recvfrom(65535)` 由来、`http2` / `http3` の受信も同じ)。QUIC の `max_udp_payload_size` は設定しておらず RFC 9000 の既定 65527 になる。したがって本検査は低レベル API を直接使う利用者と、将来の呼び出し経路追加に対する防御である
- 送信系 (`send_stream_data` / `send_data` / `send_datagram`) はアプリが任意長を渡せる唯一の経路であり、上限が無い

## 設計方針

- 上限値は h2 / h3 と同値の 1 MiB (1048576 バイト) に揃える。層ごとに閾値が食い違うと「どの API が何バイトまで通るか」が利用者から見て分からなくなるため、既存の決定を引き継ぐ。判定は `>` とし 1 MiB ちょうどは受理する
- 検査は `nb::bytes` から `std::vector` へコピーする前に行い、超過は `std::invalid_argument` (Python の `ValueError`) として通知する
- 検査の実装は新しい共通ヘッダに切り出し、h2 / h3 の既存実装もそこへ寄せる。`header_convert.h` は「nghttp2 / nghttp3 のヘッダー表現への変換ヘルパー」と目的が限定されているため使わない。新ヘッダは `nghttp2` / `nghttp3` に依存せず、`<cstddef>` / `<stdexcept>` / `<string>` だけで完結させる (quic.cpp は `header_convert.h` を include していないため、共通化の受け皿が別途必要)
- `ping` の `opaque_data` は本来 8 バイト固定だが、これは上限検査とは別の検証であり本 issue では扱わない
- 高レベル API の docstring は、上限が利用者から見える経路 (低レベル API を直接使う場合) に限って追記する

## 完了条件

- 8 経路すべてで 1 MiB 超の入力が `ValueError` になり、1 MiB ちょうどの入力は受理される
- `tests/` に境界値テストを追加し、各経路で 1 MiB と 1 MiB + 1 を検証する
- `skills/webtransport-py/SKILL.md` の該当節に上限を追記する
- 全テストが通過する

## 解決方法

(着手時に記入)
