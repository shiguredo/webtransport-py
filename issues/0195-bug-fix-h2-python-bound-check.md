# WebTransport over HTTP/2 の Python 境界入力にサイズ上限を設ける

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-python-bound-check
- Polished: {YYYY-MM-DD}

## 目的

`receive` / `send_stream_data` / `send_datagram` の Python → C++ 経路は `nb::bytes` から `std::vector` へ無検査でコピーするため、ローカル呼び出し側が巨大値を渡すとメモリを大量確保する。ワイヤ由来ではなくローカル由来の脅威であり、issue 0158 から分離した。単回呼び出し上限 1 MiB (0158 のワイヤ上限と整合。HTTP DATAGRAM 自体が MTU 由来の上限を持つため実害なし) を超える値は `ValueError` にする。

## 現状

- `src/bindings/webtransport_h2.cpp` のバインディング層の `receive` / `send_stream_data` / `send_datagram` はいずれもサイズ検査なしでコピーする
- shiguredo-python 規約は Python ↔ C++ 間のデータ受け渡しに入力サイズ上限の検査を求める

## 設計方針

- 3 経路の単回呼び出しに 1 MiB 上限検査を追加し、超過は `std::invalid_argument` を投げて `ValueError` にする (nanobind の既定翻訳)
- 変更対象は `src/bindings/webtransport_h2.cpp` のみとする

## 完了条件

- 上限超過の呼び出しが `ValueError` になること
- 通常サイズの送受信が影響を受けないこと
- `tests/` に境界値テストを追加すること
- 既存のテスト全 834 件が引き続き通過すること
