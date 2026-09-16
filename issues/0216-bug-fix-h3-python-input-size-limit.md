# h3 の Python 境界入力にサイズ上限が無い

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-python-input-size-limit
- Polished: 2026-09-16

## 目的

`shiguredo-python` スキルは「Python ↔ C++ 間のデータ受け渡しでは、入力サイズの上限を明示的に検査すること」を定めている。WebTransport over HTTP/3 のバインディングは `nb::bytes` を無検査で `std::vector` へコピーしており、規約違反かつ h2 との非対称になっている。これは Python 呼び出し境界の防御であり、`ngtcp2` / `nghttp3` が渡すチャンクの上限を定めるものではない (高レベル API の受信ループは `quic.Connection` のイベントデータをそのまま渡すが、その 1 チャンクは QUIC パケット単位で、DATAGRAM は `max_datagram_frame_size` で有界)。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::receive_stream_data` / `receive_datagram` / `send_stream_data` / `send_datagram` の各バインディングは、`nb::bytes` をそのまま `std::vector<uint8_t>` のコンストラクタへ渡している。h3 の `nb::bytes` 入力はこの 4 経路で全部である
- `src/bindings/webtransport_h2.cpp` は `check_python_input_size` を持ち、`H2Session::receive` / `send_stream_data` / `send_datagram` の各バインディングで 1 MiB の上限を課している。上限は `kMaxPythonInputBytes` として定義され、既定のカプセルペイロード上限と同値であることを `static_assert` で保証している
- `src/bindings/webtransport_h3.cpp` に同種の検査は存在しない
- h3 でもカプセル自体は使う (`WT_CLOSE_SESSION` によるセッション終了など) が、h3 のアプリケーションデータはネイティブ QUIC ストリームと QUIC DATAGRAM で運び、DATA フレームは CONNECT ストリーム上のカプセル輸送にのみ現れる。このため h2 の `wt_max_capsule_payload_size` に相当する単回カプセルペイロード上限の設定が `H3SessionConfig` に無い。`H3SessionConfig::wt_pre_accept_buffer_limit` は受理前のストリーム 1 本あたりの累計受信バイト上限 (既定 65536) であり、単回入力の上限とは意味が異なる
- 境界値のテストも存在しない (`tests/test_webtransport_h2_input_limit.py` に相当するものが h3 に無い)

## 設計方針

- 上限値は h2 の `kMaxPythonInputBytes` と同値の 1 MiB (1048576 バイト) に揃える。h2 側も 0158 の既定カプセルペイロード上限と同値という根拠で 1 MiB に決めており、その決定を引き継ぐ。h3 には h2 の `static_assert` に相当する担保対象が無いため、h2 と同値である理由は定数のコメントに書く
- 検査は `nb::bytes` から `std::vector` へコピーする前に行い、超過は `std::invalid_argument` (Python の `ValueError`) として通知する。上限ちょうどの入力は受理する (h2 と同じ `>` 判定)
- 検査の実装は h2 の `check_python_input_size` と同じ形にする。`header_convert.h` のような共通ヘッダへ寄せられるが、共通化は本 issue の目的ではないため重複を許容して h3 側に実装してもよい

## 完了条件

- 1 MiB を超える入力が 4 経路すべてで `ValueError` になり、1 MiB ちょうどの入力は 4 経路すべてで受理される
- `receive_stream_data` / `receive_datagram` / `send_stream_data` / `send_datagram` の 4 経路すべてに検査が入る
- `tests/test_webtransport_h3_input_limit.py` を追加し、4 経路それぞれで 1 MiB と 1 MiB + 1 を検証する
- `skills/webtransport-py/SKILL.md` の `h3.Session` に入力上限を追記し、高レベル `h3.Client` / `h3.Server` の `send_stream_data` / `send_datagram` にも `ValueError` を記載する
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/bindings/webtransport_h3.cpp` に上限値の定数と検査関数を追加し、4 つのバインディングで呼ぶ。あわせて 4 つの `.def` の docstring に「data が 1 MiB 超の場合は ValueError」を追記する (docstring の正本は C++ 側であり、`src/webtransport/webtransport_ext/h3.pyi` は `make develop` で再生成して追跡分を更新する)
- `src/webtransport/h3/client.py` / `server.py` の `send_stream_data` / `send_datagram` に `Raises: ValueError` を追記する (h2 の 0195 と同じ形。`h3.pyi` は生成物であり高レベル層の docstring を含まない)
- `tests/test_webtransport_h3_input_limit.py` を追加し、4 経路それぞれで 1 MiB と 1 MiB + 1 を検証する
- `skills/webtransport-py/SKILL.md` の `h3.Session` の節に、4 経路が 1 MiB 超で `ValueError` を送出することを追記する
- `CHANGES.md` の `## develop` には新しいエントリを追加しない。h2 の Python 境界入力上限 (0195) の単独エントリは 0186 で「WebTransport over HTTP/3 と HTTP/2 の受信で入力検証・上限・フロー制御違反の検知が漏れていた問題を修正する (カプセルサイズ / 入力サイズ / ストリーム数 / フロー制御値の減少・逆転)」へ畳まれており、その「入力サイズ」が該当する。h3 は同じ変更の非対称を埋めるもので、利用者から見た最終差分はこの行に含まれる
