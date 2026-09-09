# WebTransport over HTTP/3 の受理前ストリームとデータグラムのバッファリング上限がない

- Created: 2026-09-10
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-buffering-limits
- Polished: {YYYY-MM-DD}

## 目的

draft-ietf-webtrans-http3-16 Section 4.6 は "To avoid resource exhaustion, endpoints MUST limit the number of buffered streams and datagrams" と定める。h3 バインディングは `H3Session::send_datagram` が `pending_datagrams_.push_back` するだけで上限がなく、受理前ストリームのバッファにも上限がない。リモート発火のメモリ DoS 経路を塞ぐ。

## 現状

- `src/bindings/webtransport_h3.cpp` の `H3Session::send_datagram` は上限チェックなしで `pending_datagrams_.push_back(std::move(datagram))` する
- `H3SessionConfig` に受理前ストリーム / データグラムのバッファ上限がない
- `stream_buffers_` / `pending_sends_` / `pending_datagrams_` に上限を示す定数は存在しない
- nghttp3 は `NGHTTP3_ERR_WT_BUFFERED_STREAM_REJECTED` / `NGHTTP3_WT_BUFFERED_STREAM_REJECTED` を提供しており、受理前ストリームの拒否機構はライブラリ側に存在する

## 設計方針

- `h3.Config` に `max_buffered_datagrams: int` を追加し、`send_datagram` でキュー上限を超えるデータグラムを enqueue 段階で drop する (破棄件数をログ出力する)
- 受理前ストリームの上限は nghttp3 の `WT_BUFFERED_STREAM_REJECTED` 機構と `h3.Config` の追加項目で実現する。nghttp3 の設定 API との整合を実装時に確認する
- 破棄・拒否はいずれも Session 層の受理前バッファを対象とし、カプセル送出後の nghttp3 内部バッファは対象外とする
- CODEBASE.md の「nghttp3 をフォークしないこと」に従い、ライブラリ側の改修が必要な場合はバインディング層で吸収できる設計にする

## 完了条件

- データグラムキュー上限を超えたデータグラムが enqueue 段階で drop されること
- 受理前ストリームが上限を超えた場合に `WT_BUFFERED_STREAM_REJECTED` で拒否されること (または nghttp3 の制約で実現できない場合は pending へ切り替える)
- 上限値が `h3.Config` から設定できること
- 上限を超えた場合の単体テストを追加すること
- 既存のテストがすべて通ること
