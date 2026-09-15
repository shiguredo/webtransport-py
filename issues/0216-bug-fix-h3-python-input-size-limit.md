# h3 の Python 境界入力にサイズ上限が無い

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-python-input-size-limit
- Polished: {YYYY-MM-DD}

## 目的

`shiguredo-python` スキルは「Python ↔ C++ 間のデータ受け渡しでは、入力サイズの上限を明示的に検査すること」を定めている。WebTransport over HTTP/3 のバインディングは `nb::bytes` を無検査で `std::vector` へコピーしており、規約違反かつ h2 との非対称になっている。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` / `receive_datagram` / `send_stream_data` / `send_datagram` の各バインディングは、`nb::bytes` をそのまま `std::vector<uint8_t>` のコンストラクタへ渡している
- `src/bindings/webtransport_h2.cpp` は `check_python_input_size` を持ち、`H2Session::receive` / `send_stream_data` / `send_datagram` の各バインディングで 1 MiB の上限を課している。上限は `kMaxPythonInputBytes` として定義され、既定のカプセルペイロード上限と同値であることを `static_assert` で保証している
- `src/bindings/webtransport_h3.cpp` に同種の検査は存在しない
- 境界値のテストも存在しない (`tests/test_webtransport_h2_input_limit.py` に相当するものが h3 に無い)

## 設計方針

- `src/bindings/webtransport_h2.cpp` の `check_python_input_size` と同じ検査を h3 にも入れる。上限値は h2 と揃えるか、h3 のカプセル上限に合わせて別に定めるかを決めて定数化する
- 検査は `nb::bytes` から `std::vector` へコピーする前に行い、超過は `std::invalid_argument` (Python の `ValueError`) として通知する
- 共通化できるなら `header_convert.h` のような共通ヘッダへ寄せる。共通化は本 issue の目的ではないため、重複を許容して h3 側に実装してもよい

## 完了条件

- 上限を超える入力が `ValueError` になり、上限ちょうどの入力は受理される
- `receive_stream_data` / `receive_datagram` / `send_stream_data` / `send_datagram` の 4 経路すべてに検査が入る
- 上限と上限 + 1 を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h3.cpp` に上限値の定数と検査関数を追加し、4 つのバインディングで呼ぶ
- `src/webtransport/webtransport_ext/h3.pyi` の docstring に上限を記載する (スタブは再生成する)
- `tests/test_webtransport_h3_input_limit.py` を追加し、4 経路それぞれで上限と上限 + 1 を検証する
