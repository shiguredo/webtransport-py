# 一次資料の参照を refs/ と一致させる

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/update-fix-spec-references
- Polished: 2026-09-15

## 目的

コードとコメントが参照している仕様の版数と節番号の一部が、`refs/` に置いている一次資料と一致していない。参照がずれていると、実装の根拠を確認するときに誤った節を読むことになる。あわせて、参照されている仕様のうち `refs/` に一次資料が無いものを整備して、ローカルで裏取りできる状態にする。

## 現状

draft の版数:

- `draft-ietf-quic-reliable-stream-reset-09` を参照している箇所は 8 つある
  - `src/bindings/quic.cpp` の 5 箇所: `QuicConnection::setup_server_early_data` (early data context にも含めて恒常的に広告する旨)、`QuicConnection::initialize_client`、`QuicConnection::initialize_server`、`QuicConnection::initialize_server_from_packet`、`QuicConnection::reset_stream` (データ配信とロス時の再送の旨)
  - `src/bindings/quic.h` の `QuicConnection::enable_reset_stream_at`
  - `tests/test_e2e_webtransport_h3_low_level.py` の 2 箇所 (Section 5.3 と Section 5)
  - `src/bindings/quic.cpp` の `reset_stream` はコメント中で `draft-ietf-quic-reliable-` と `stream-reset-09` に行折り返しされているため、`grep -rn "draft-ietf-quic-reliable-stream-reset-09"` では検出できない
- `refs/quic/` に置いているのは `draft-ietf-quic-reliable-stream-reset-10.txt` のみである
- `-09` と `-10` は節番号の構成こそ同じだが、参照箇所が要約している文面が変わっている。`-10` では Section 3 の 0-RTT の MUST が「両エンドポイントはこの transport parameter の値を記憶する」から「サーバーが広告したかどうかを記憶する (RFC 9000 Section 7.4.1)」に変わり、Section 5 (および 5.2) の Reliable Size 0 の等価性に「データ配信の目的では」という限定が付き、Section 5.3 の遷移記述から flow control credit の言及が外れて Section 4 への参照に変わり、References の参照先 draft も変わっている

RFC の節番号:

- QPACK のエラーコードを「RFC 9204 Section 8.3」と説明している箇所が 4 つある。8.3 は IANA 登録の節であり、値の定義は Section 6 (Error Handling) にある
  - `src/webtransport/http3/constants.py` の 2 箇所
  - `src/bindings/http3.cpp` の 2 箇所 (`nghttp3_error_to_h3_wire_code` の doc コメントと、0x0200 を返す分岐のインラインコメント)。doc コメントは `RFC 9204` と `Section 8.3` に行折り返しされているため、`grep -n "RFC 9204 Section 8.3"` では検出できない
- `src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` の `on_stream_end` 関連コメントが「RFC 9114 Section 4.1 のメッセージフレーミングと Section 6 のフレーム境界と QUIC STREAM_DATA 境界の独立性」と書いている。Section 6 (Stream Mapping and Usage) は「QUIC STREAM フレームによるフレーミングは HTTP フレーミング層からは見えない」と述べており、この独立性の根拠として正しい。フレーム自体の定義は Section 7 (HTTP Framing Layer) と 7.1 (Frame Layout) にあるため、引用を補って出典を明確にする

引用文:

- `src/webtransport/http3/constants.py` の `H3_GENERAL_PROTOCOL_ERROR` の説明が、`refs/h3/rfc9114.txt` の該当文と読点 1 つ分だけ一致していない

`refs/` に実物が無い引用:

- コードとドキュメントが参照している RFC のうち、次のものは `refs/` に一次資料が無いためローカルで裏取りできない。参照箇所数は実測値である
  - RFC 9110 (17 箇所) / RFC 9113 (46 箇所) / RFC 9218 (17 箇所): HTTP/2 の実装と設定の根拠
  - RFC 9297 (11 箇所): WebTransport over HTTP/2 の DATAGRAM (Capsule Protocol) の根拠
  - RFC 3629 (4 箇所) / RFC 6454 (4 箇所) / RFC 7301 (2 箇所) / RFC 7541 (1 箇所) / RFC 8941 (1 箇所) / RFC 9220 (3 箇所): 個別の記述の根拠
  - RFC 7540: コードからの引用は無く、nghttp2 の設定名 (`SETTINGS_NO_RFC7540_PRIORITIES`) に RFC 番号が含まれるだけである (引用は `skills/webtransport-py/SKILL.md` の 1 箇所のみ)

## 設計方針

- 版数は `refs/` の実物に合わせる。`refs/` に無い版を参照し続けない
- 版数を合わせるだけでなく、`-09` と `-10` で文面が変わった箇所 (0-RTT の記憶対象、Reliable Size 0 の等価の限定、Section 5.3 の遷移記述と flow control の参照) は、コメントの日本語記述を `-10` の文面に合わせて更新する
- 節番号は `refs/` の実物に合わせる。RFC 9114 Section 6 の引用は、フレーミング境界の独立性の根拠として正しいため残し、フレームの定義を示す Section 7 / 7.1 を併記する
- 引用文は逐語一致させる。要約として書く場合は引用符を使わない
- `refs/` への追加は、HTTP/2 の実装と設定の根拠として複数箇所から参照されている RFC 9110 / 9113 / 9218 と、Capsule Protocol の根拠である RFC 9297 の 4 件を対象とする。個別の記述 1 〜 4 箇所の根拠にとどまる他の RFC は追加しない
- 追加は `update-refs` の手順に従う。同スキルはユーザー承認なしのダウンロードを禁じているため、取得 URL と配置先を提示して承認を得る。承認が得られない場合は追加せず、その旨を解決方法に記録して本 issue を完了とする

## 完了条件

- `src/` `tests/` `examples/` `skills/` を対象に、行折り返しを含む検索 (`grep -rn "reliable-stream-reset-"`) で `-09` の参照が 0 件になる (`issues/` と `refs/` は対象外)
- `-10` の文面に合わせて、`-09` を参照していた 8 箇所の日本語記述が見直されている
- RFC 9204 の節番号が 4 箇所すべて Section 6 になる
- RFC 9114 の引用が、Section 6 (フレーミング境界の独立性) を残したまま Section 7 / 7.1 (フレームの定義) を併記する形になる
- `H3_GENERAL_PROTOCOL_ERROR` の引用が `refs/h3/rfc9114.txt` と逐語一致する
- RFC 9110 / 9113 / 9218 / 9297 を `refs/` に配置し、引用箇所から参照できる (承認が得られなかった場合は、追加しなかった理由が解決方法に記録されている)
- 全テストが通過する

## 解決方法

- `src/bindings/quic.cpp` の 5 箇所 (`setup_server_early_data` / `initialize_client` / `initialize_server` / `initialize_server_from_packet` / `reset_stream`)、`src/bindings/quic.h` の `enable_reset_stream_at`、`tests/test_e2e_webtransport_h3_low_level.py` の 2 箇所の `-09` を `-10` に更新し、あわせて各コメントの日本語記述を `-10` の文面に合わせて見直す
- `src/webtransport/http3/constants.py` の 2 箇所と `src/bindings/http3.cpp` の 2 箇所 (doc コメントと 0x0200 のインラインコメント) の節番号を Section 6 に修正する
- `src/webtransport/http3/client.py` / `server.py` の引用に Section 7 / 7.1 を併記する
- `/update-refs refs/h3/` の手順で RFC 9110 / 9113 / 9218 / 9297 の現行版を確認し、取得 URL と配置先を提示して承認を得たうえで `refs/h3/` (Capsule Protocol は `refs/webtrans/` でもよい) に配置する。配置先は既存の `refs/` の構成に合わせる
- `refs/` を追加した場合は、`issues/0229-update-decide-sdist-policy.md` の sdist 同梱物の記述 (refs/ の件数) が変わるため、同 issue の更新が必要である旨を申し送る
