# HTTP/2 で WT_CLOSE_SESSION 受信後に close_session で応答すると SessionClosed が二重発火する

- Created: 2026-08-12
- Completed: 2026-08-14
- Branch: feature/fix-h2-wt-close-session-double-close
- Polished: 2026-08-14

## 目的

WebTransport over HTTP/2 で、ピアが WT_CLOSE_SESSION カプセルを送ってセッションを終了した場合に、受信側のアプリが `close_session` で応答すると SessionClosed イベントが 2 回発火する問題を修正する。closed issue 0070 の設計方針で「スコープ外の既存の挙動」として残された問題であり、`handle_wt_close_session` がエントリを削除しないことが原因。

## 現状

- `src/bindings/webtransport_h2.cpp` の `handle_wt_close_session` は SessionClosed イベントを発火して `is_terminated` / `is_established` のフラグを更新するが、`wt_sessions_` からエントリを削除しない (「process_capsules がまだバッファを参照している可能性がある」という理由で削除を先送りし、HTTP/2 ストリーム close 時の `on_stream_close_callback` に委ねている)
- コンプライアントなピアは WT_CLOSE_SESSION 送出後に必ず END_STREAM を送る (draft-ietf-webtrans-http2-15 Section 6.12 の MUST)。受信側アプリが `close_session` で応答すると自側も END_STREAM を送出し、ストリームの両ハーフが閉じて `on_stream_close_callback` が発火する
- `on_stream_close_callback` はエントリが存在する限り SessionClosed を発火するため、WT_CLOSE_SESSION 受信 (1 回目) + 両ハーフクローズ (2 回目) で SessionClosed が 2 回発火する (実測確認済み。1 回目はカプセルの error_code、2 回目は error_code 0)
- 0070 の実装 (END_STREAM 検知の `handle_end_stream`) はエントリを削除するため、END_STREAM のみの経路では二重発火しない。二重発火の機構は 1 つである: WT_CLOSE_SESSION を受信した側 (1 回目: カプセルの error_code) が `close_session` で応答すると、両ハーフクローズで `on_stream_close_callback` が 2 回目 (error_code 0) を発火する。応答した側で発生し、どちらの側が先に `close_session` しても同じ機構で二重発火する (先に `close_session` した側は `is_established` が false のため後続の WT_CLOSE_SESSION を処理せず、`on_stream_close_callback` 由来の 1 回のみ。実測確認済み)
- `process_capsules` はループ内で毎回 `get_wt_session` を再取得しており (現行実装)、`handle_wt_close_session` 内でエントリを削除しても参照切れの危険はない (レビューで確認済み。削除先送りは現状の実装では不要な防衛)

## 設計方針

- `handle_wt_close_session` で `wt_sessions_` からエントリを削除する (0070 の `handle_end_stream` と同じ「エントリ不在で塞がる」論理。`http2_stream_buffers_` の破棄も同様に行う)。破棄により、終了を学習した後にキュー済みのカプセルが送出されなくなる (0063 の「終了を学習する前にキュー済みの送出はスコープ外 (送出され得る)」の原則が、本経路ではキュー済みの送出も破棄される挙動に変わる)。破棄で失われるのは受信側アプリが終了前に送出した未 flush のカプセル (データグラム等) であり、エントリ削除後の `close_session` 応答は no-op のため応答カプセルは発生しない。先に `close_session` した側は `is_established` が false のため WT_CLOSE_SESSION を受信しても処理せず (受信ゲート)、エントリ削除自体が起きない
- エントリ削除により、以後の `on_stream_close_callback` / `close_session` / `send_datagram` / `send_stream_data` / `open_stream` / `reset_stream` がエントリ不在で自然に塞がる (`stop_sending` / `drain_session` は `get_wt_session` を確認せずカプセルを送出するため塞がれないが、本 issue の対象外。0070 と同じ扱い)
- 削除後も `is_terminated` / `is_established` のフラグ更新は維持する (エントリ削除前に実施)。フラグ更新とエントリ削除は同一処理内で完了し、間にアプリの送信機会はないため、フラグは実質的に機能しないが、`close_session` (フラグのみ立ててエントリを残す) との対称性と将来の変更への防衛として維持する
- アプリの `close_session` 応答はエントリ削除により no-op になり、自側の END_STREAM 応答は送出されなくなる (draft-ietf-webtrans-http2-15 Section 6.12 の受信者側 MUST「END_STREAM フレームで応答してストリームを閉じる」は未実装のまま残る)。ストリームは half-closed (remote) のまま接続終了まで残る (0070 の END_STREAM 経路と同じ既知の制約)
- 代替案 (WT_CLOSE_SESSION 受信時のフラグを `on_stream_close_callback` 側で確認して SessionClosed の発火を抑止する) は不採用: エントリが残るため `send_stream_data` 等の送信 API が塞がれず、0070 の「エントリ不在で塞がる」方針と不整合になる
- 既存テスト `test_send_datagram_after_recv_wt_close_session_ignored` (tests/test_webtransport_h2_datagram.py) が「WT_CLOSE_SESSION 受信後に send_datagram が無視される」ことを検証しており、エントリ削除後もこの挙動が維持されることを確認する。既存テスト `test_end_stream_after_wt_close_session_no_double` (tests/test_webtransport_h2_end_stream.py) も、セッション終了の検知経路が `is_terminated` チェックからエントリ不在に置き換わるが、SessionClosed が 1 回だけ発火する結果は維持されることを確認する
- 変更対象は `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h` (`handle_wt_close_session` のエントリ削除とコメントの更新。修正後に陳腐化する既存コメント: 削除先送りの理由を述べた `handle_wt_close_session` 内の「セッション本体の削除は HTTP/2 ストリーム close 時に行う (process_capsules がまだバッファを参照している可能性がある)」、「唯一の違いはエントリ削除の有無」と述べた `handle_end_stream` の並置コメント、`handle_end_stream` 冒頭の「既に is_terminated のセッション (WT_CLOSE_SESSION 受信済み・ローカル close_session 済み) はスキップする」のコメント、`send_datagram` の実装コメントと h の docstring)、テスト (既存の `test_end_stream_after_wt_close_session_no_double` の docstring の経路記述の更新を含む)、`CHANGES.md` (## develop セクションへの [FIX] エントリ。0070 の END_STREAM 検知エントリと区別できる文言で)

## 完了条件

- WT_CLOSE_SESSION 受信後にアプリが `close_session` で応答しても、SessionClosed が 1 回だけ発火する (二重発火しない)
- WT_CLOSE_SESSION 受信後の `send_datagram` が no-op になる (既存の挙動が維持される。`is_terminated` フラグによる抑止がエントリ不在による抑止に置き換わる)
- WT_CLOSE_SESSION 受信後の `close_session` / `send_stream_data` が no-op になる (エントリ削除による新規の挙動)
- エントリ削除が機能していることの間接検証として、WT_CLOSE_SESSION 受信後に `close_session` が no-op (WT_CLOSE_SESSION の再送出なし) になることを確認する
- モックなしの Sans-IO テストで検証できる (0063 で新設した h2 用 Sans-IO ヘルパーを再利用する)

## 解決方法

`handle_wt_close_session` で `wt_sessions_` からエントリを削除し、`http2_stream_buffers_` を破棄するようにした (0070 の `handle_end_stream` と同じ「エントリ不在で塞がる」論理)。

- エントリ削除により、以後の `on_stream_close_callback` / `close_session` / `send_datagram` / `send_stream_data` / `open_stream` / `reset_stream` がエントリ不在で塞がれ、WT_CLOSE_SESSION 受信 (1 回目) + 両ハーフクローズ時の `on_stream_close_callback` (2 回目) による SessionClosed の二重発火が解消された
- draft-15 Section 6.12 の受信者 MUST (WT_CLOSE_SESSION 受信時に END_STREAM フレームで応答してストリームを閉じる) を実装した。設計方針では「未実装のまま残る」としていたが、ユーザー指示により実装した。`end_stream_pending_` + `nghttp2_session_resume_data` で応答の END_STREAM を送出し、コンプライアントなピアとの間でストリームが両ハーフクローズで閉じる (同時ストリーム枠を消費し続けない)。`on_stream_close_callback` でも `end_stream_pending_` を消去するようにした (ストリームクローズ後の stale エントリ防止)
- 設計方針の「削除後もフラグ更新は維持する (close_session との対称性)」は、レビューで削除直前のフラグ書き込みが観測不能な死に書き込みであると指摘されたため削除した (`handle_end_stream` と同じくフラグを書かない)
- `send_datagram` の実装コメントと h の docstring、`close_session` のコメント、`WtSessionInfo::is_terminated` のコメントを新挙動に合わせて更新した (WT_CLOSE_SESSION 受信はエントリ削除で表現し、`is_terminated` を立てる経路はローカル close_session / reject_session の 2xx 送出のみ)
- テスト: `tests/test_webtransport_h2_end_stream.py` に 7 件追加した (END_STREAM 応答の送出 / イニシエーター側の SessionClosed 1 回 / 終了前キュー済みカプセルの破棄 / close_session 応答時の二重発火なし / close_session の no-op / send_stream_data の no-op / 非コンプライアントピアへの END_STREAM 応答)。既存テストの docstring (test_webtransport_h2_datagram.py / test_webtransport_h2_end_stream.py) を新挙動に合わせて更新した

全テスト (622 件) が通ることを確認済み。
