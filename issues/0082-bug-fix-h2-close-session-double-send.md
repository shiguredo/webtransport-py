# HTTP/2 の close_session を二重に呼ぶと WT_CLOSE_SESSION capsule が二重送出される問題を修正する

- Created: 2026-08-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-close-session-double-send
- Polished: 2026-08-15

## 目的

ローカル `close_session` の二重呼び出しで WT_CLOSE_SESSION capsule が 2 個ワイヤへ送出される問題を修正する。`send_datagram` / `stop_sending` / `drain_session` / `send_stream_data` / `reset_stream` が終了済みセッションへの送出を no-op 化されたことで、`close_session` が「終了後もカプセルをキューし続ける」唯一の送信 API になった非対称点を解消する。

## 現状

- `src/bindings/webtransport_h2.cpp` の `close_session` は `get_wt_session` の確認のみで `is_terminated` を確認しない。ローカル `close_session` はエントリを残したまま `is_terminated` を立てるため、2 回目以降の呼び出しでも `get_wt_session` が成功し、WT_CLOSE_SESSION capsule が再びキューされる
- 二重呼び出しでは、flush 前は WT_CLOSE_SESSION が 2 個ワイヤへ送出され、flush 後は 2 個目のカプセルが `http2_stream_buffers_` に残留する。ピア側 (`handle_wt_close_session`) は 1 個目でエントリを削除するため 2 個目は処理されない。二重送出の抑止は仕様上の MUST ではなく実装ポリシー (終了済みセッション ID 宛の送出を無視する他の送信 API との整合) であり、本対応はそのポリシーの拡張である
- `reject_session` の実装コメントは「`close_session` は is_terminated を確認せずカプセルをキューするため滞留し得る (誤用限定の挙動)」と明記している
- 0074 は WT_CLOSE_SESSION 受信後の受信側の二重発火を修正しており、本 issue はローカル `close_session` の二重呼び出し (送出側) で範囲が異なる

## 設計方針

- `close_session` の冒頭に `is_terminated` の確認を追加し、終了済み時は no-op にする (`send_datagram` / `stop_sending` / `drain_session` / `send_stream_data` / `reset_stream` と同一のガード構成)
- `send_stream_data` のフロー制御違反時 (FLOW_CONTROL_ERROR) は `close_session` を内部から呼ぶが、その時点では `is_terminated` が立っていないためガードで塞がれないことを確認する (既に終了済みの場合は `send_stream_data` 冒頭のガードで内部呼び出し自体が発生しない)
- ガード追加で陳腐化するコメントを更新する:
  - `reject_session` の実装コメントと `webtransport_h2.h` の docstring の「is_terminated を立てて塞ぐ API 列挙」に `close_session` を加え、`reject_session` の実装コメント (docstring には無い) の「`close_session` は is_terminated を確認せずカプセルをキューするため滞留し得る」の記述を新挙動に合わせて更新する
  - `close_session` 自身の「後始末カプセル WT_CLOSE_SESSION 自体は塞がれない」記述を「2 回目以降の呼び出しは冒頭ガードで塞がれる」点を反映して修正する
  - `send_datagram` の実装コメントの「ここと同じガードを自前で持つ」API 列挙に `close_session` を加える
- `src/bindings/webtransport_h2.h` の `close_session` の docstring に「終了済みセッション ID への呼び出しは無視される」旨を追記する
- テスト: ローカル `close_session` 後 (flush 前) の二重呼び出しでワイヤ上の WT_CLOSE_SESSION が 1 個のみであることをワイヤ部分列チェックで検証し、生存セッションの 1 回の `close_session` が従来どおり送出される回帰ピンも追加する (0079 / 0080 のテストと同様の Sans-IO 構成とワイヤ部分列チェックを使う)。フロー制御違反時の内部呼び出しは、送信クレジットが対向の SETTINGS 由来のためピア側 (サーバー) の config の `wt_initial_max_stream_data` を小さな正の値にし (0 にするとフォールバックで自側 config が使われ縮小しない)、`send_stream_data` で超過させるテストで確認する (既存テストにフロー制御違反を発生させるものは無い)。高レベル API (`src/webtransport/h2/client.py` / `server.py`) の `close_session` は C++ 層への委譲のみのためコード変更不要
- 変更対象: `src/bindings/webtransport_h2.cpp` (ガード追加・コメント更新) / `src/bindings/webtransport_h2.h` (docstring 更新) / テスト / `CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- `close_session` を 2 回呼んでも WT_CLOSE_SESSION capsule がワイヤへ 1 個だけ送出される (2 回目は no-op)。flush 後も no-op となり、カプセルを `http2_stream_buffers_` に残留させない
- 生存セッションの 1 回の `close_session` は従来どおり送出される
- `send_stream_data` のフロー制御違反時の内部 `close_session` 呼び出しは従来どおり動作する (フロー制御超過を発生させるテストで確認する)
- 全テストが通る
