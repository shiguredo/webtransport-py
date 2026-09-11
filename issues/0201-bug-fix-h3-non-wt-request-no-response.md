# WebTransport over HTTP/3 のサーバーが非 WebTransport リクエストに応答せず 405 にも Allow が付かない

- Created: 2026-09-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-non-wt-request-no-response
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 3.2 は、extended CONNECT で `:protocol=webtransport-h3` を受けた際に対象リソースが WebTransport 非対応なら 405 を返す SHOULD を定め、405 の定義として参照する RFC 9110 Section 15.5.6 は Allow ヘッダーを MUST とする。h3 のサーバーは `H3Session::end_headers_cb` で `is_connect && is_webtransport` 以外のリクエストを無応答のまま破棄し、`H3Session::reject_session` は 405 でも `Allow` を付けない。HTTP/2 では closed/0173 で非 WT リクエストへの 405 + `Allow: CONNECT` を実装済みであり、h3 を対称の挙動にする。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::end_headers_cb` は、`is_connect && is_webtransport` 不成立のサーバー側リクエストに対して `pending_qpack_blocked_fin_stream_ids_` と `pending_headers_` の掃除だけを行い、応答を送出しない (通常の HTTP リクエストも CONNECT 非 webtransport も同様)
- `H3Session::reject_session` は `nghttp3_nv` に `:status` のみを積むため、405 を渡しても `Allow` が付かない
- `H3Session::reject_session` は status_code の範囲検証をしない (h2 側は 200-599 のみ許容)
- h3 の `:protocol` 判定は `webtransport-h3` と `webtransport` の両方を受ける (実ブラウザ互換のための既知の逸脱。draft-16 Section 2.1.2 と RFC 9220 Section 3 の 501 SHOULD との関係はコードコメントに記載済み)
- 高レベル `h3.Server` の拒否 API は open/0137 の担当であり、本 issue は低レベル `H3Session` の挙動を対象とする
- closed/0173 の h2 側は、非 WT リクエストへの 405 を「draft の SHOULD 対象外だが WebTransport 専用エンドポイントとしての実装ポリシー」と位置づけている

## 設計方針

- `H3Session::end_headers_cb` の非 WT サーバー側リクエスト分岐で `reject_session(stream_id, 405)` を呼び、405 応答でストリームを終端する
- `H3Session::reject_session` で 405 のときのみ `Allow: CONNECT` を応答ヘッダーに含める (RFC 9110 Section 15.5.6 の MUST)
- 意味論は h2 の closed/0173 と揃える。draft の 405 SHOULD の対象は extended CONNECT + `:protocol=webtransport-h3` であり、非 WT リクエストへの 405 は WebTransport 専用エンドポイントとしての実装ポリシーである
- テストは Sans-IO で応答ヘッダーを観測する。第一候補は h2 の 0173 と同型の「`http3.Connection` クライアント + `h3.Session` サーバー」構成とし、実装時に観測手段を確定する
- nghttp3 の submit 失敗を握り潰している点は open/0204 (h2 側) と同様の課題として残す。本 issue では成功経路のみを対象とする
- 変更対象: `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h` / テスト / `CHANGES.md` の develop への FIX エントリ

## 完了条件

- 非 WT リクエストに対して `:status` 405 と `allow: CONNECT` を持つ応答が返り、ストリームが滞留しないこと
- `reject_session(session_id, 405)` を呼んだ場合も応答に `allow: CONNECT` が付くこと
- 非 WT リクエストの 405 と `reject_session(405)` の allow を表明する Sans-IO テストを追加すること
- `CHANGES.md` の develop に FIX エントリが追加されていること
- 既存のテストが引き続き通過すること
