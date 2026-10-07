# セッション単位の TLS keying material exporter (EXPORTER-WebTransport) が無い

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/add-webtransport-tls-exporter

## 目的

draft-ietf-webtrans-http3-16 Section 4.8 と draft-ietf-webtrans-http2-15 Section 5.3 が定めるセッション単位の TLS keying material exporter をアプリから利用できるようにする。現状は exporter を導出する API が無く、セッションに紐づく鍵素材を前提とするアプリ (Concealed HTTP Auth 系など) が使えない。

## 現状

- draft-ietf-webtrans-http3-16 Section 4.8 は「If the application requests an exporter for a given WebTransport session with a specified label and context, the resulting exporter MUST be a TLS exporter as defined in Section 7.5 of [RFC8446] with the label set to "EXPORTER-WebTransport" and the context set to the serialization of the "WebTransport Exporter Context" struct」と定める。同 Section に構造体 (WebTransport Session ID (64) / WebTransport Application-Supplied Exporter Label Length (8) / ラベル / WebTransport Application-Supplied Exporter Context Length (8) / context) が定義されている。draft-ietf-webtrans-http2-15 Section 5.3 も同旨である
- `src/` に exporter 関連のシンボルが無い (`exporter` / `EXPORTER-WebTransport` / `export_keying_material` のいずれもヒット 0 件)。`quic.Connection` の `export_session_ticket` / `export_0rtt_transport_params` はセッション再開用であり exporter ではない
- 影響: セッション単位の鍵素材導出を前提とするアプリが h3 / h2 のどちらでも実装できない。条件付き MUST のため、アプリが要求しない限り仕様違反にはならない (機能欠落としての起票)

## 設計方針

- quic 層に TLS exporter を公開する。`src/bindings/quic.cpp` の `QuicConnection` は接続の `SSL*` を持つため、BoringSSL の `SSL_export_keying_material` (RFC 8446 Section 7.5) を呼ぶ経路を追加する。ハンドシェイク完了前の呼び出しは失敗させる
- WebTransport の context の直列化は C++ 側で行う。`WebTransport Exporter Context` の構造体 (Session ID 64 bit / ラベル長 8 bit / ラベル / context 長 8 bit / context) を組み立て、ラベルは固定で `EXPORTER-WebTransport`、WebTransport のセッション ID は呼び出し元から渡す
- 高レベルの API は h3 / h2 で同じ形にする。引数は (ラベル, context, 長さ)、戻り値は導出鍵 (bytes)
- h2 は TCP + TLS (Python `ssl`) であり、`ssl` モジュールに exporter API が無いため提供できない可能性が高い。着手時に確認し、提供できない場合は h3 (QUIC) のみを対象にして、その旨と理由を本 issue と `skills/webtransport-py/SKILL.md` に明記する
- 変更対象: `src/bindings/quic.cpp` / `.h`、`src/webtransport/webtransport_ext/quic.pyi`、`src/webtransport/quic/*`、`src/webtransport/h3/*` (および提供できる場合のみ `src/webtransport/h2/*`)、`tests/`、`skills/webtransport-py/SKILL.md`

## 完了条件

- 同じセッション ID と同じラベル / context で導出した鍵が、両エンドポイントで一致する
- セッション ID が異なると導出鍵が一致しない (セッション分離が効いている)
- 導出が RFC 8446 Section 7.5 の exporter と一致する (ハンドシェイク完了後の実通信で確認する)
- ハンドシェイク完了前の呼び出しが例外になる (未定義動作にしない)
- 提供できない層がある場合は、対象範囲と理由を本 issue の記録と `skills/webtransport-py/SKILL.md` に明記する
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する

## 解決方法
