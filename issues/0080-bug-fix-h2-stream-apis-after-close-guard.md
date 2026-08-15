# HTTP/2 の send_stream_data / reset_stream がローカル close_session 後に終了済みセッション宛にカプセルを送出する問題を修正する

- Created: 2026-08-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-stream-apis-after-close-guard
- Polished: {YYYY-MM-DD}

## 目的

HTTP/2 の `send_stream_data` / `reset_stream` は `get_wt_session` の確認のみで `is_terminated` を確認しないため、ローカル `close_session` 後に呼ぶと終了済みセッション宛に WT_STREAM / WT_RESET_STREAM capsule をワイヤへ送出してしまう (flush 前) か、`http2_stream_buffers_` に残留させてメモリを保持し続ける (flush 後)。`send_datagram` と揃えて no-op にする。

## 現状

- `src/bindings/webtransport_h2.cpp` の `send_stream_data` と `reset_stream` は `get_wt_session` のみ確認し、`is_terminated` を確認しない
- ローカル `close_session` 後はエントリが残存したまま `is_terminated` のみ立つため、`close_session` 前に開いたストリームが存在すれば `get_wt_session` は成功し、`send_stream_data` / `reset_stream` はカプセルをキューする:
  - flush 前: WT_STREAM / WT_RESET_STREAM capsule が WT_CLOSE_SESSION の後ろに積まれてワイヤへ送出される (終了済みセッション宛の誤送出)
  - flush 後: カプセルが `http2_stream_buffers_` に残留する
- ピアの END_STREAM 受信後・非 2xx 拒否受信後はエントリ削除で塞がれるため、誤送出は「アプリ自身が `close_session` を呼んだ後」に限られる (0079 のレビューで実測確認済み)
- 0079 で `stop_sending` / `drain_session` は `send_datagram` と同一のガード (`get_wt_session` + `is_terminated`) で塞がれたが、`send_stream_data` / `reset_stream` には非対称性が残る
- `close_session` の実装コメントは「`close_session` / `reset_stream` は `is_terminated` を確認せずカプセルをキューするため滞留し得る」と述べており、`reset_stream` がローカル `close_session` 後 flush 前は「滞留」ではなく「ワイヤ送出」される事実は明記されていない

## 設計方針

- `send_stream_data` / `reset_stream` の `get_wt_session` 確認に `is_terminated` の確認を追加し、終了済み時は no-op にする (`send_datagram` / `stop_sending` / `drain_session` と同一のガード構成。チェックを `send_capsule` に置かない理由は `send_datagram` のコメントと同様)
- `close_session` の実装コメント (is_terminated で塞がれる API の列挙) と、`src/bindings/webtransport_h2.h` の `send_stream_data` / `reset_stream` の docstring (「終了済みセッション ID への送信は無視される」旨) を更新する
- テスト: ローカル `close_session` 後 (flush 前・後) に `send_stream_data` / `reset_stream` を呼んでもワイヤに送出されないことを検証する。生存セッションの回帰ピンも追加する (0079 のテストと同様の Sans-IO 構成とワイヤ部分列チェックを使う)
- 変更対象: `src/bindings/webtransport_h2.cpp` (ガード追加・コメント更新) / `src/bindings/webtransport_h2.h` (docstring 更新) / テスト / `CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- ローカル `close_session` 後 (flush 前・後) に `send_stream_data` / `reset_stream` を呼んでもカプセルがワイヤに送出されない (no-op)
- 生存セッションの `send_stream_data` / `reset_stream` は従来どおり送出される
- 全テストが通る
