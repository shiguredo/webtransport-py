# WebTransport over HTTP/2 の Python 境界入力にサイズ上限を設ける

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-python-bound-check
- Polished: 2026-09-09

## 目的

`receive` / `send_stream_data` / `send_datagram` の Python → C++ 経路は `nb::bytes` から `std::vector` へ無検査でコピーするため、ローカル呼び出し側が巨大値を渡すとメモリを大量確保する。ワイヤ由来ではなくローカル由来の脅威であり、issue 0158 から分離した。3 経路の単回呼び出しに生の入力バイト数 1 MiB の上限を設け、超過は `ValueError` にする。1 MiB は 0158 の既定カプセルペイロード上限 (`H2SessionConfig::wt_max_capsule_payload_size`、既定 1 MiB) と同値で、1 MiB ちょうどの DATAGRAM を受理する既存境界値テストがある。

## 現状

- `src/bindings/webtransport_h2.cpp` のバインディング層の `receive` / `send_stream_data` / `send_datagram` はいずれもサイズ検査なしでコピーする
- 0158 の `wt_max_capsule_payload_size` は受信カプセルのペイロード長に対する検査であり、Python 入力のサイズ検査ではない
- shiguredo-python 規約は Python ↔ C++ 間のデータ受け渡しに入力サイズ上限の検査を求める

## 設計方針

- 3 経路の単回呼び出しに生の入力バイト数 (`data.size()`) 1 MiB 上限検査を追加し、超過は `std::invalid_argument` を投げて `ValueError` にする (nanobind の既定翻訳)。上限は `>` で判定し 1 MiB ちょうどは通す
- 本上限は `data.size()` に対するローカル防御であり、ワイヤのカプセルペイロード長との厳密一致は求めない。`send_stream_data` はエンコード時に Stream ID の varint を前置するため、`data.size()` が 1 MiB 以下でもカプセルは 1 MiB をわずかに超え得る (H2 の DATAGRAM capsule に MTU 由来の上限はない。draft-15 Section 6.11 の MTU 記述は中間装置が QUIC データグラムへ転送する場合のもの)
- コード変更対象は `src/bindings/webtransport_h2.cpp` のみ。これに加えてテストと `CHANGES.md` の develop への FIX エントリを付ける

## 完了条件

- 3 経路それぞれで 1 MiB 超の入力が `ValueError` になること
- 1 MiB ちょうどの入力はローカル検査を通り、通常サイズの送受信が影響を受けないこと
- `tests/` に境界値テストを追加すること
- `CHANGES.md` の develop に FIX エントリが追加されていること
- 既存のテスト全 976 件が引き続き通過すること
