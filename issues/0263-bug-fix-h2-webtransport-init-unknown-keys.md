# h2 の WebTransport-Init が未知キーを無視せず 400 で拒否する

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-webtransport-init-unknown-keys

## 目的

draft-ietf-webtrans-http2-15 Section 4.3.2 の「Unknown keys and parameters in the dictionary MUST be ignored.」に従い、未知キーを含む `WebTransport-Init` を拒否しないようにする。現状は既知キー (u / bl / br) が正しくても、未知キーの値が Integer でないだけで CONNECT が 400 で拒否され、拡張キーを載せる実装と相互接続できない。

## 現状

- draft-ietf-webtrans-http2-15 Section 4.3.2 は「If any of these keys are present but contain invalid values, the endpoint MUST reject the CONNECT request with a 4xx status code. Unknown keys and parameters in the dictionary MUST be ignored.」と定める。`WebTransport-Init` は RFC 8941 の Dictionary であり、値には sf-integer 以外 (sf-token / sf-boolean / sf-decimal / sf-string 等) も入り得る
- `src/bindings/webtransport_h2.cpp` の `H2Session::parse_webtransport_init` はキー名を読んだあと、値を `while (...) { if (!std::isdigit(...)) { return false; } ++pos; }` で走査する。キー名の判定 (u / bl / br) は値の解釈後に行われるため、未知キーでも値が Integer 以外なら false を返す
- 呼び出し側の `H2Session::end_headers_cb` (クライアントの応答受信時) は false を `h2_session->reject_session(stream_id, 400)` に変換する
- パラメータ (`;` 以降) はキー名を問わず読み飛ばしており、こちらは仕様どおりである
- 影響: `WebTransport-Init: u=262144, foo=bar` のような RFC 8941 として妥当なリクエストが 400 で拒否される。将来の拡張キーや他実装が載せる未知キーで相互接続に失敗する
- 既存の `WebTransport-Init` のテストは `tests/test_webtransport_h2_settings_limit.py`、`tests/test_webtransport_h2_initial_flow_control_fallback.py`、`tests/prop_webtransport_h2.py` にある

## 設計方針

- 値をキーごとに解釈する。既知キー (u / bl / br) は Integer のみを受理し、Integer 以外は 4xx で拒否する (Section 4.3.2 の「known key の invalid value」)
- 未知キーは値の型を問わず無視する。値のスキップは RFC 8941 の Dictionary の構文に従い、パラメータ (`;` 以降) も含めて読み飛ばす。引用符付きの sf-string 内の `,` / `;` / 空白をキー区切りと誤認しないようにする
- 構文として壊れている入力 (キー名が空、`=` の欠落など) は拒否する。現行の「形式不正は拒否」の方針を維持する
- 変更対象: `src/bindings/webtransport_h2.cpp`、`tests/test_webtransport_h2_settings_limit.py` (および必要なら専用テストファイル)、`skills/webtransport-py/SKILL.md` (該当記述があれば)

## 完了条件

- 未知キーに sf-token / sf-boolean / sf-decimal / sf-string を載せた `WebTransport-Init` が 400 で拒否されず、既知キーの値が正しく反映される
- 既知キー (u / bl / br) の値が Integer 以外なら従来どおり 400 で拒否される
- パラメータ付きの既知キーは従来どおり無視され、値が使われる
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する

## 解決方法
