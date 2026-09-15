# h3 の Python 境界入力にサイズ上限が無い

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-python-input-size-limit
- Polished: 2026-09-15

## 目的

`shiguredo-python` スキルは「Python ↔ C++ 間のデータ受け渡しでは、入力サイズの上限を明示的に検査すること」を定めている。WebTransport over HTTP/3 のバインディングは `nb::bytes` を無検査で `std::vector` へコピーしており、規約違反かつ h2 との非対称になっている。これは Python 呼び出し境界の防御であり、`ngtcp2` / `nghttp3` が渡すチャンクの上限を定めるものではない。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` / `receive_datagram` / `send_stream_data` / `send_datagram` の各バインディングは、`nb::bytes` をそのまま `std::vector<uint8_t>` のコンストラクタへ渡している。h3 の `nb::bytes` 入力はこの 4 経路で全部である
- `src/bindings/webtransport_h2.cpp` は `check_python_input_size` を持ち、`H2Session::receive` / `send_stream_data` / `send_datagram` の各バインディングで 1 MiB の上限を課している。上限は `kMaxPythonInputBytes` として定義され、既定のカプセルペイロード上限と同値であることを `static_assert` で保証している
- `src/bindings/webtransport_h3.cpp` に同種の検査は存在しない
- h3 にはカプセルが無く (Capsule Protocol を使うのは h2)、`H3SessionConfig` にもカプセルペイロード上限に相当する設定は無い。`H3SessionConfig::wt_pre_accept_buffer_limit` は受理前の累計受信バイト上限であり、単回入力の上限とは意味が異なる
- 境界値のテストも存在しない (`tests/test_webtransport_h2_input_limit.py` に相当するものが h3 に無い)

## 設計方針

- 上限値は h2 の `kMaxPythonInputBytes` と同値の 1 MiB (1048576 バイト) に揃える。h2 側も 0158 の既定カプセルペイロード上限と同値という根拠で 1 MiB に決めており、その決定を引き継ぐ。h3 には h2 の `static_assert` に相当する担保対象が無いため、h2 と同値である理由は定数のコメントに書く
- 検査は `nb::bytes` から `std::vector` へコピーする前に行い、超過は `std::invalid_argument` (Python の `ValueError`) として通知する。上限ちょうどの入力は受理する (h2 と同じ `>` 判定)
- 検査の実装は h3 のカプセル上限に合わせるのではなく、h2 の `check_python_input_size` と同じ形にする。`header_convert.h` のような共通ヘッダへ寄せられるが、共通化は本 issue の目的ではないため重複を許容して h3 側に実装してもよい

## 完了条件

- 1 MiB を超える入力が `ValueError` になり、1 MiB ちょうどの入力は受理される
- `receive_stream_data` / `receive_datagram` / `send_stream_data` / `send_datagram` の 4 経路すべてに検査が入る
- `tests/test_webtransport_h3_input_limit.py` を追加し、4 経路それぞれで 1 MiB と 1 MiB + 1 を検証する
- `skills/webtransport-py/SKILL.md` の `h3.Session` に入力上限を追記する
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h3.cpp` に上限値の定数と検査関数を追加し、4 つのバインディングで呼ぶ
- `src/webtransport/webtransport_ext/h3.pyi` の docstring に上限を記載する (スタブは再生成して追跡分を更新する)
- `tests/test_webtransport_h3_input_limit.py` を追加し、4 経路それぞれで 1 MiB と 1 MiB + 1 を検証する
- `skills/webtransport-py/SKILL.md` の `h3.Session` の節に、4 経路が 1 MiB 超で `ValueError` を送出することを追記する
