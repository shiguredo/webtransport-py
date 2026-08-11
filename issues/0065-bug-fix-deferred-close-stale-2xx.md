# 遅延クローズ保留中に未送信の 2xx レスポンスが送出される

- Created: 2026-08-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-deferred-close-stale-2xx
- Polished: {YYYY-MM-DD}

## 目的

受理前 FIN の遅延クローズ保留中にセッション終了 (WT_CLOSE_SESSION 受信等) が発生した場合、終了済みセッションの CONNECT ストリームに未送信の 2xx レスポンスが後から書き出される問題を修正する。

## 現状

- 0058 の遅延クローズは、未送信の 2xx を破棄しないため 2xx レスポンスの書き出し完了 (`stream_flushed`) を待ってから `close_stream` を実行する。フロー制御等で 2xx が書き出せない間は保留される
- 保留中に `close_session` (WT_CLOSE_SESSION 送出) や `recv_wt_close_session_cb` (WT_CLOSE_SESSION 受信) でセッション終了が発生した場合 (実測確認済み):
  - セッション終了自体は 1 回だけ検知される (二重発火はしない)
  - しかし、その後の `get_streams_to_send` で未送信の 2xx (33 バイト) が書き出される
- これは仕様の MUST (新しいデータグラム・ストリームの禁止) には反しないが、終了済みセッションの CONNECT ストリームへの無意味な送信であり、draft-ietf-webtrans-http3-16 Section 6 の「WT_CLOSE_SESSION 受信後に CONNECT ストリームへ追加データを送ることは H3_MESSAGE_ERROR の対象」という方向性と矛盾する

## 設計方針

- 候補 1: 遅延クローズループで「`session_ids_` に含まれる場合のみ `close_stream`」する条件を追加する (WT_CLOSE_SESSION 受信で削除済みのセッションは対象外にする)
- 候補 2: セッション終了経路 (`recv_wt_close_session_cb` / `close_session`) で保留集合 (`pre_accept_fin_accepted_session_ids_` 等) のエントリを清掃し、未送信 2xx の扱いを明確にする
- 未送信 2xx の破棄方法 (nghttp3 に 2xx をキャンセルする API があるか) は調査対象とする
- 変更対象は `src/bindings/webtransport_h3.cpp` / `src/bindings/webtransport_h3.h`、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- 遅延クローズ保留中にセッション終了 (WT_CLOSE_SESSION 送出・受信) が発生しても、未送信の 2xx がワイヤに送出されない (または破棄される)
- 通常の受理前 FIN の遅延クローズ (2xx 書き出し完了後に close_stream) は影響を受けない
- モックなしの Sans-IO テストで検証できる (block_stream で 2xx の書き出しを止めた状態で WT_CLOSE_SESSION を送る構成)
