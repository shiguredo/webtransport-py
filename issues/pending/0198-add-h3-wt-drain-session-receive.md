# WebTransport over HTTP/3 に WT_DRAIN_SESSION の受信通知がない

- Created: 2026-09-10
- Completed: {YYYY-MM-DD}
- Branch: feature/add-h3-wt-drain-session-receive
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 4.7 の WT_DRAIN_SESSION (0x78ae) は、h2 側では `H2Session::handle_wt_drain_session` と `H2EventType::SessionDraining` でアプリに通知されるが、h3 側には受信通知経路がない。h3 でも受信時にアプリへ通知できるようにする。

## 現状

- nghttp3 は `NGHTTP3_EXFR_CPSL_WT_DRAIN_SESSION (0x78AE)` の定数を持つが、`nghttp3_conn.c` のカプセル分岐には `WT_CLOSE_SESSION` のみがあり、`WT_DRAIN_SESSION` を処理するコールバック (`recv_wt_drain_session` 相当) が存在しない
- `src/bindings/webtransport_h3.cpp` にも WT_DRAIN_SESSION の受信ハンドラがなく、`H3EventType` に相当するイベント型もない
- 対照: `src/bindings/webtransport_h2.cpp` の `handle_wt_drain_session` と `H2EventType::SessionDraining` で実装済み

## 設計方針 (案)

- nghttp3 に WT_DRAIN_SESSION の受信コールバックが追加された場合、`H3Session` で受信解釈し、新規イベント型 `H3EventType::SessionDraining` を発火する
- 高レベル `h3.Client` に `on_session_draining` コールバックを追加する (h2 側と対称)
- nghttp3 が内部で当該カプセルを消費してしまう現状ではバインディング層から観測できないため、nghttp3 側の対応待ちとする (CODEBASE.md の「nghttp3 をフォークしないこと」に従う)

## 完了条件

- nghttp3 が WT_DRAIN_SESSION の受信経路を提供した時点で、この issue を reopened して設計方針を確定する
- 受信で `on_session_draining` が発火すること
- e2e テストを追加すること
- 既存のテストがすべて通ること

## pending にした理由

- nghttp3 に WT_DRAIN_SESSION (0x78AE) の受信コールバックが無く、`nghttp3_conn.c` が内部で当該カプセルを消費するため、バインディング層から観測できない。CODEBASE.md の「nghttp3 をフォークしないこと」に従い、nghttp3 側の対応待ちとする
- nghttp3 が受信経路を提供した時点で reopened にして設計方針を確定する
