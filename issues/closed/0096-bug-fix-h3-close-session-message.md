# WebTransport over HTTP/3 の WT_CLOSE_SESSION メッセージ送信トリミング・受信検証 (1024 バイト・UTF-8) を実装する

- Created: 2026-08-18
- Completed: 2026-08-25
- Branch: feature/fix-h3-close-session-message
- Polished: 2026-08-24

## 目的

draft-ietf-webtrans-http3-16 Section 6 の WT_CLOSE_SESSION メッセージに関する MUST を実装する。対象は (a) 送信側の 1024 バイト制限と UTF-8 文字境界トリミング、(b) 受信側の 1024 バイト超過・不正 UTF-8 の検知と H3_MESSAGE_ERROR でのリセット。H2 側の同種対応は issue 0085 (送信側トリミング) / 0100 (受信側検証) が別途担当しており、本 issue は H3 側を担当する。

## 現状

- **送信側**: `src/bindings/webtransport_h3.cpp` の `H3Session::close_session` は error_message を無検証で `nghttp3_conn_close_wt_session` へ渡す。nghttp3 は送信時にメッセージ長が 1024 バイトを超えると `NGHTTP3_ERR_INVALID_ARGUMENT` を返す (トリミングはしない)。現行の `close_session` は `rv != 0` で無条件に return するため、1024 バイト超のメッセージでは WT_CLOSE_SESSION が送出されず、セッションも閉じない (アプリへは何も通知されない)。違反メッセージがワイヤへ載ることはないが、「its length MUST NOT exceed 1024 bytes」を満たす送出が現行ではできない
- **受信側**: `H3Session::recv_wt_close_session_cb` はメッセージを無検証でアプリへ渡す。nghttp3 は 1024 バイト超過を `NGHTTP3_ERR_H3_MESSAGE_ERROR` で検知するのみで、リセットは送出しない。UTF-8 妥当性は検証しないため、以下が未達:
  - 「is not valid UTF-8, the receiver MUST reset the stream with code H3_MESSAGE_ERROR」
  - 1024 バイト超過受信時の「MUST reset the stream with code H3_MESSAGE_ERROR」(nghttp3 の検知だけでリセットが発生しない)
  - さらに、コールバックが 0 を返す正常経路では nghttp3 が `NGHTTP3_ERR_WT_SESSION_GONE` を内部で捕捉し、`WT_SESSION_GONE` (0x170D7B68) でのセッションシャットダウンを実行する。そのため不正 UTF-8 をコールバックで検知しても、コールバックが 0 を返すと 0x170D7B68 が先行し、リセットコードを H3_MESSAGE_ERROR (0x010E) にはできない (このとき読み取りは正当値として返り、Error イベントも積まれない)
- 高レベル API (client.py / server.py) には error_message 付きの close_session が存在せず、現状の 1024 バイト超送出は C++ バインディング直接利用時に限られる

## 設計方針

- **変更対象**: `src/bindings/webtransport_h3.cpp` の `H3Session::close_session` (送信側トリミング) / `H3Session::recv_wt_close_session_cb` (受信側検知) / `H3Session::receive_stream_data` (リセット処理) / `H3Session::accept_session` (confirm 経路のリセット処理) / テスト / CHANGES.md
- **送信側**: error_message をバイト単位で 1024 に切り詰めた後、末尾が不完全な UTF-8 シーケンスなら直前の文字境界まで後退させる (issue 0085 の H2 側と同じ手法)。1024 バイトちょうどは合法 (MUST NOT exceed の超過のみ違反)。トリミングは nghttp3 の `NGHTTP3_ERR_INVALID_ARGUMENT` を避けるためにも必須となる
- **受信側**: 1024 バイト超過・不正 UTF-8 の両方で CONNECT ストリームを H3_MESSAGE_ERROR (0x010E) でリセットする。検知経路が 2 系統あることに注意する:
  - **1024 バイト超過**: `recv_wt_close_session_cb` は発火せず、`nghttp3_conn_read_stream2` の戻り値 `NGHTTP3_ERR_H3_MESSAGE_ERROR` で検知される。`receive_stream_data` の `consumed < 0` 分岐で CONNECT ストリームのリセットを送出する。この経路ではコールバック未発火のため `session_ids_` にセッションが残存しており、CONNECT ストリームの特定はセッション ID から行える (再入問題なし)
  - **不正 UTF-8**: `recv_wt_close_session_cb` 内で検知し、**コールバックは非 0 を返す**。コールバックが 0 を返すと nghttp3 が 0x170D7B68 でセッションを先にシャットダウンし、0x010E に矯正できないため。コールバック非 0 では `NGHTTP3_ERR_CALLBACK_FAILURE` が `nghttp3_conn_read_stream2` から返り、`consumed < 0` 分岐でリセット処理へ合流できる。コールバック内で nghttp3 を呼ぶと再入になるため、検知はコールバック内で行い、リセットの実行は `receive_stream_data` が `read_stream2` から戻った後の `consumed < 0` 分岐で行う
  - **accept_session の confirm 経由**: 受理前にバッファされた WT_CLOSE_SESSION が `process_blocked_wt_stream_data` で同期処理される経路では `receive_stream_data` が呼ばれない。1024 バイト超過は confirm 自体が `NGHTTP3_ERR_H3_MESSAGE_ERROR` で失敗し、不正 UTF-8 はコールバックの非 0 戻り (CALLBACK_FAILURE) により confirm が失敗する。どちらも `accept_session` の確認失敗分岐 (既存の `discard_stale_2xx()` 呼び出しがある `rv != 0` 分岐) でリセット処理を実行する
  - **コールバック非 0 の影響**: コールバック非 0 では nghttp3 のセッションシャットダウン (0x170D7B68 でのリセット) が発動しないため、Section 6 の MUST (セッション終了時の残留データストリームの WT_SESSION_GONE でのリセット) は下記のリセット送出手段 (close_stream 適用時に nghttp3 が発火させる reset_stream_cb) に委ねる。CONNECT ストリームの 0x010E リセット、残留データストリームの 0x170D7B68 リセット、セッション後始末 (erase_session_streams / session_ids_ 削除 / セッション終了イベント) の順序を設計に含める
  - **リセットの送出手段 (公開 API の制約)**: nghttp3 の公開ヘッダー (nghttp3.h) に `nghttp3_conn_abort_stream` の宣言はなく、CONNECT ストリーム自身の reset_stream_cb を発火させる公開 API は存在しない (`nghttp3_conn_close_stream` / `nghttp3_conn_close_stream2` は `conn_delete_stream` を呼ぶのみで、CONNECT ストリームに対しては reset_stream_cb を発火させない。残留データストリームには `conn_unlink_wt_session` が自動で reset_stream_cb を発火させる)。そのため CONNECT ストリームの 0x010E リセットは、H3Session が `H3EventType::ResetStream` (error_code = 0x010E) を明示 push し、高レベル層の既存変換 (RESET_STREAM イベント → `quic_connection.reset_stream`) を経由してワイヤへ送出する。nghttp3 側のストリーム状態は `nghttp3_conn_close_stream` で CONNECT ストリームの消去を伝える (残留データストリームの 0x170D7B68 リセットはこのとき nghttp3 が発火する reset_stream_cb を経由し、同じ変換で送出される)
- テストを追加する: 送信側の UTF-8 境界トリミング (1024 バイト超・マルチバイト文字)、受信側の不正 UTF-8・1024 バイト超過で CONNECT ストリームが H3_MESSAGE_ERROR (0x010E) の RESET_STREAM になること。Sans-IO 構成 (既存のワイヤ検査テストと同様) で検証する
- 0131 との切り分け: 0131 (open) は同一の負値分岐で接続エラー (`nghttp3_err_is_fatal()` が真) の `closed_ = true` 化を担当する。本 issue が扱う `NGHTTP3_ERR_H3_MESSAGE_ERROR` (-611) はストリームレベルのエラーで closed_ にせず、CONNECT ストリームのリセット処理を追加する。エラー値で分離し、両者は協調して付ける
- 変更内容を CHANGES.md の `## develop` に [FIX] として記載する

## 完了条件

- 1024 バイト超・マルチバイト文字を含むエラーメッセージが UTF-8 文字境界で切り詰められて送出される (ワイヤ上の Application Error Message が有効な UTF-8・1024 バイト以下)
- 不正な UTF-8 メッセージの受信で CONNECT ストリームが H3_MESSAGE_ERROR (0x010E) でリセットされる
- 1024 バイト超過のメッセージ受信で CONNECT ストリームが H3_MESSAGE_ERROR (0x010E) でリセットされる
- それぞれのテストが追加され通る

## 解決方法

- **送信側** (`src/bindings/webtransport_h3.cpp` の `H3Session::close_session`): error_message をバイト単位で 1024 に切り詰めた後、不完全な UTF-8 シーケンスなら文字境界まで後退させてから `nghttp3_conn_close_wt_session` へ渡す (draft-16 Section 6 の MUST 準拠。1024 バイト超のメッセージをそのまま渡すと nghttp3 が `NGHTTP3_ERR_INVALID_ARGUMENT` を返して黙って失敗していた)
- **受信側**: `recv_wt_close_session_cb` で不正 UTF-8 (および防御として 1024 バイト超) を検知し、コールバックの非 0 戻り (`NGHTTP3_ERR_CALLBACK_FAILURE` 経由) と保留により `nghttp3_conn_read_stream2` の負値分岐でリセット処理へ合流させる。1024 バイト超・4 バイト未満の不正な長さは nghttp3 が `NGHTTP3_ERR_H3_MESSAGE_ERROR` を返すため、その負値分岐で直接リセット処理する。リセットは `handle_wt_close_session_error`で行い、nghttp3 には `close_stream` で CONNECT ストリームの消去を伝え、0x010E の QUIC RESET_STREAM は `ResetStream` イベントの明示 push で高レベル層の既存変換に委ねる (nghttp3 の公開 API には reset_stream_cb を発火させる手段がないため)。`accept_session` の confirm 前バッファ経由 (1024 バイト超・不正 UTF-8) も確認失敗分岐で同処理を行う (0131 (open) は接続エラー時の閉鎖を担当し、本 issue はストリームエラーのリセットだけを担当する)
- テスト: `tests/test_webtransport_h3_close_session_message.py` (送信側のトリミング 2 本 / 受信側の 1024 超・不正 UTF-8・4 バイト未満・1024 ちょうど・空メッセージ・max 長の不正 UTF-8・confirm 前バッファ 2 経路) と `tests/prop_webtransport_h3.py` (1024 バイト超の任意エラーメッセージを検証する PBT)
