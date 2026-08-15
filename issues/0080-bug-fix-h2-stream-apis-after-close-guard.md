# HTTP/2 の send_stream_data / reset_stream がローカル close_session 後に終了済みセッション宛にカプセルを送出・残留させる問題を修正する

- Created: 2026-08-15
- Completed: 2026-08-15
- Branch: feature/fix-h2-stream-apis-after-close-guard
- Polished: 2026-08-15

## 目的

HTTP/2 の `send_stream_data` / `reset_stream` は `get_wt_session` の確認のみで `is_terminated` を確認しないため、ローカル `close_session` 後に呼ぶと終了済みセッション宛に WT_STREAM / WT_RESET_STREAM capsule をワイヤへ送出してしまう (flush 前) か、`http2_stream_buffers_` に残留させる (flush 後)。`send_datagram` と揃えて no-op にする。

## 現状

- `src/bindings/webtransport_h2.cpp` の `send_stream_data` と `reset_stream` は `get_wt_session` のみ確認し、`is_terminated` を確認しない
- ローカル `close_session` 後はエントリが残存したまま `is_terminated` のみ立つため、`get_wt_session` は成功し、`send_stream_data` (close_session 前に開いたストリームが存在する場合) / `reset_stream` (ストリームの有無に関わらず) はカプセルをキューする:
  - flush 前: WT_STREAM / WT_RESET_STREAM capsule が WT_CLOSE_SESSION の後ろに積まれてワイヤへ送出される (終了済みセッション宛の誤送出)
  - flush 後: カプセルが `http2_stream_buffers_` に残留する (送出されない。ピアの END_STREAM 応答 (Section 6.12 の受信者 MUST) が来るまで保持され、来ない場合は保持し続ける)
- ピアの END_STREAM 受信後・非 2xx 拒否受信後はエントリ削除で塞がれるため、誤送出は「アプリ自身が `close_session` を呼んだ後」に限られる (エントリ削除経路の送出なしは 0079 のテストで確認済み。1xx を挟んだ非 2xx 拒否等、エントリが残る既知の制約の経路は `send_datagram` と同様に対象外)
- 0079 で `stop_sending` / `drain_session` は `send_datagram` と同一のガード (`get_wt_session` + `is_terminated`) で塞がれたが、`send_stream_data` / `reset_stream` には非対称性が残る
- `reject_session` の実装コメントは「`close_session` / `reset_stream` は `is_terminated` を確認せずカプセルをキューするため滞留し得る」と述べており (reject_session はデータプロバイダ未登録のため滞留のみで送出されない)、`reset_stream` がローカル `close_session` 後 flush 前は「滞留」ではなく「ワイヤ送出」される事実は明記されていない

## 設計方針

- `send_stream_data` / `reset_stream` の `get_wt_session` 確認に `is_terminated` の確認を追加し、終了済み時は no-op にする (`send_datagram` / `stop_sending` / `drain_session` と同一のガード構成。チェックを `send_capsule` に置かない理由は `send_datagram` のコメントと同様)
- `reject_session` の実装コメントと `send_datagram` の実装コメント (どちらも is_terminated で塞がれる API の列挙を含む) を、`send_stream_data` / `reset_stream` も塞がれるようになる点を反映して更新する。`src/bindings/webtransport_h2.h` の `send_stream_data` / `reset_stream` の docstring には「終了済みセッション ID への送信は無視される」旨を追記し、`reject_session` の docstring は is_terminated で塞がれる API の列挙に `send_stream_data` / `reset_stream` を加えて更新する
- テスト: ローカル `close_session` 後 (flush 前) に `send_stream_data` / `reset_stream` を呼んでもワイヤに送出されないことを検証する (ワイヤ送出の有無で修正を確認できるのは flush 前のみ。flush 後の残留は内部状態のため公開 API からは観測できない)。生存セッションの回帰ピンも追加する (0079 のテストと同様の Sans-IO 構成とワイヤ部分列チェックを使う)
- 変更対象: `src/bindings/webtransport_h2.cpp` (ガード追加・コメント更新) / `src/bindings/webtransport_h2.h` (docstring 更新) / テスト / `CHANGES.md` (## develop セクションへの [FIX] エントリ)。高レベル API (`src/webtransport/h2/client.py` / `server.py`) の `send_stream_data` / `reset_stream` は C++ 層への委譲のみのためコード変更不要 (Python 層 docstring への追記は任意)

## 完了条件

- ローカル `close_session` 後 (flush 前) に `send_stream_data` / `reset_stream` を呼んでもカプセルがワイヤに送出されない (no-op)。flush 後も no-op となり、カプセルを `http2_stream_buffers_` に残留させない
- 生存セッションの `send_stream_data` / `reset_stream` は従来どおり送出される
- 全テストが通る

## 解決方法

- `src/bindings/webtransport_h2.cpp` の `send_stream_data` / `reset_stream` の冒頭に `get_wt_session` + `is_terminated` の確認を追加し、終了済み時は no-op にした (`send_datagram` / `stop_sending` / `drain_session` と同一のガード構成)
- `reject_session` の実装コメントと `send_datagram` の実装コメントを「`send_stream_data` / `reset_stream` も is_terminated で塞がれる」内容に更新した
- `src/bindings/webtransport_h2.h` の `send_stream_data` / `reset_stream` の docstring に「終了済みセッション ID への送信は無視される」旨を追記し、`reject_session` の docstring の列挙に `send_stream_data` / `reset_stream` を加えた
- `tests/test_webtransport_h2_send_stream_data_reset_stream.py` を新規作成し、テスト 5 本を追加した (ローカル `close_session` 後 (flush 前) の `send_stream_data` / `reset_stream` (fin 有無) がワイヤへ送出されないことの検証 3 本、生存セッションの回帰ピン 2 本)。ガード無効化ビルドで送出抑止テスト 2 本が失敗することを確認し、修正の検証として機能することを実証した
- `CHANGES.md` の `## develop` セクションに [FIX] エントリを追加した
- 全テスト (643 本) が通ることを確認した
