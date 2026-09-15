# 一次資料の参照を refs/ と一致させる

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/update-fix-spec-references
- Polished: {YYYY-MM-DD}

## 目的

コードとコメントが参照している仕様の版数と節番号の一部が、`refs/` に置いている一次資料と一致していない。参照がずれていると、実装の根拠を確認するときに誤った節を読むことになる。

## 現状

draft の版数:

- RESET_STREAM_AT 関連のコメントが `draft-ietf-quic-reliable-stream-reset-09` を参照している (`src/bindings/quic.cpp` の `initialize_client` / `initialize_server` / `initialize_server_from_packet` と `src/bindings/quic.h` の `enable_reset_stream_at`、`tests/test_e2e_webtransport_h3_low_level.py`)
- `refs/quic/` に置いているのは `draft-ietf-quic-reliable-stream-reset-10.txt` のみである。参照箇所の内容 (transport parameter `reset_stream_at`、Reliable Size、Section 5 の記述) は版数以外に差分が無いことを確認済みである

RFC の節番号:

- `src/webtransport/http3/constants.py` が QPACK のエラーコードを「RFC 9204 Section 8.3」と説明している。8.3 は IANA 登録の節であり、値の定義は Section 6 (Error Handling) にある
- `src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` の `on_stream_end` 関連コメントが「RFC 9114 Section 6 のフレーム境界」と書いている。フレームは Section 7 (HTTP Framing Layer) と 7.1 (Frame Layout) にある。Section 6 は Stream Mapping and Usage である

引用文:

- `src/webtransport/http3/constants.py` の `H3_GENERAL_PROTOCOL_ERROR` の説明が、`refs/h3/rfc9114.txt` の該当文と読点 1 つ分だけ一致していない

`refs/` に実物が無い引用:

- コードとドキュメントが引用している RFC のうち、次のものは `refs/` に一次資料が無いためローカルで裏取りできない
  - RFC 3629 / 6454 / 7301 / 7540 / 7541 / 8941 / 9110 / 9113 / 9218 / 9220 / 9297
- 特に RFC 9110 / 9113 / 9218 / 9297 は HTTP/2 実装の根拠として複数箇所で参照されている

## 設計方針

- 版数と節番号は `refs/` の実物に合わせる。`refs/` に無い版を参照し続けない
- 引用文は逐語一致させる。要約として書く場合は引用符を使わない
- `refs/` への追加は、その仕様を今後も参照し続けるものに限る。追加する場合は `update-refs` の運用に乗せる

## 完了条件

- `draft-ietf-quic-reliable-stream-reset-09` の参照が無くなり、`refs/` にある版数と一致する
- RFC 9204 と RFC 9114 の節番号が正しくなる
- `H3_GENERAL_PROTOCOL_ERROR` の引用が `refs/h3/rfc9114.txt` と逐語一致する
- `refs/` に追加する仕様を決め、追加した場合は引用箇所から参照できる
- 全テストが通過する

## 解決方法

- `src/bindings/quic.cpp` / `quic.h` / `tests/test_e2e_webtransport_h3_low_level.py` の `-09` を `-10` に更新する
- `src/webtransport/http3/constants.py` の節番号と引用文を修正する
- `src/webtransport/http3/client.py` / `server.py` の節番号を Section 7 / 7.1 に修正する
- `refs/` に追加する仕様を決めて配置する (`update-refs` の手順に従う)
