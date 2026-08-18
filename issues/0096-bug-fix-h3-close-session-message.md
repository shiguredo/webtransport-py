# WebTransport over HTTP/3 の WT_CLOSE_SESSION メッセージ送信トリミング・受信検証 (1024 バイト・UTF-8) を実装する

- Created: 2026-08-18
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h3-close-session-message
- Polished: 2026-08-18

## 目的

draft-ietf-webtrans-http3-16 Section 6 の WT_CLOSE_SESSION メッセージに関する MUST を実装する。対象は (a) 送信側の 1024 バイト制限と UTF-8 文字境界トリミング、(b) 受信側の 1024 バイト超過・不正 UTF-8 の検知と H3_MESSAGE_ERROR でのリセット。H2 側の同種対応は issue 0085 (送信側トリミング) / 0100 (受信側検証) で別途対応中であり、本 issue は H3 側を担当する。

## 現状

- **送信側**: `src/bindings/webtransport_h3.cpp` の `H3Session::close_session` は error_message を無検証で `nghttp3_conn_close_wt_session` へ渡す。nghttp3 は送信時に長さ検証もトリミングもしないため、1024 バイト超のメッセージを送ると「its length MUST NOT exceed 1024 bytes」に違反し、コンプライアントなピアから H3_MESSAGE_ERROR を受ける
- **受信側**: `H3Session::recv_wt_close_session_cb` はメッセージを無検証でアプリへ渡す。nghttp3 は 1024 バイト超過を `NGHTTP3_ERR_H3_MESSAGE_ERROR` で検知するのみで、リセットは送出しない。UTF-8 妥当性は検証しないため、以下が未達:
  - 「is not valid UTF-8, the receiver MUST reset the stream with code H3_MESSAGE_ERROR」
  - 1024 バイト超過受信時の「MUST reset the stream with code H3_MESSAGE_ERROR」(nghttp3 の検知だけでリセットが発生しない)
- 高レベル API (client.py / server.py) には error_message 付きの close_session が存在せず、現状の 1024 バイト超送出は C++ バインディング直接利用時に限られる

## 設計方針

- **変更対象**: `src/bindings/webtransport_h3.cpp` の `H3Session::close_session` (送信側トリミング) / `H3Session::recv_wt_close_session_cb` (受信側検証) / 関連する受信リセット経路 / テスト / CHANGES.md
- **送信側**: error_message をバイト単位で 1024 に切り詰めた後、末尾が不完全な UTF-8 シーケンスなら直前の文字境界まで後退させる (issue 0085 の H2 側と同じ手法)。1024 バイトちょうどは合法 (MUST NOT exceed の超過のみ違反)
- **受信側**: 1024 バイト超過・不正 UTF-8 の両方を検知し、H3_MESSAGE_ERROR でストリームをリセットする。検知経路が 2 系統あることに注意する:
  - **1024 バイト超過**: `recv_wt_close_session_cb` は発火せず、`nghttp3_conn_read_stream2` の戻り値 `NGHTTP3_ERR_H3_MESSAGE_ERROR` で検知される。`receive_stream_data` の `consumed < 0` 分岐でリセットを送出する。この経路ではコールバック未発火のため `session_ids_` にセッションが残存しており、既存の `close_stream` の CONNECT ストリーム判定が成立し得る (再入問題なし)
  - **不正 UTF-8**: `recv_wt_close_session_cb` 内で検知する。発火経路は 2 つある:
    - `nghttp3_conn_read_stream2` 経由 (通常受信)。コールバックは処理中に同期発火するため、コールバック内で nghttp3 を呼ぶと再入になる。既存パターン (`pending_stale_2xx_discard_session_ids_` 方式) と同様に、検知を保留集合へ記録し `receive_stream_data` が `read_stream2` から戻った後にリセット処理を実行する
    - `accept_session` の confirm 処理中 (受理前にバッファされた WT_CLOSE_SESSION が `process_blocked_wt_stream_data` で同期処理される経路)。この経路では `receive_stream_data` が呼ばれないため、リセット処理の実行は `accept_session` 内 (既存の `discard_stale_2xx()` 呼び出しと同じ場所) で行う
  - **コールバック戻り値の設計が必要**: `recv_wt_close_session_cb` が 0 を返すと、nghttp3 は `NGHTTP3_ERR_WT_SESSION_GONE` を戻り値として返すのみで、CONNECT ストリームのリセットは H3_MESSAGE_ERROR では行われない (既存コードは `receive_stream_data` の `consumed < 0` 分岐で Error イベントを積むだけ) ため、仕様 MUST (H3_MESSAGE_ERROR でのリセット) を満たさない。コールバックから非 0 を返すと `NGHTTP3_ERR_CALLBACK_FAILURE` になり、`receive_stream_data` の `consumed < 0` 分岐がアプリへ誤った Error イベントを積むため、この経路の扱い (Error イベントを積まない等) を設計する。また、コールバック非 0 で nghttp3 のセッション破棄を止めた場合、セッション所属データストリームの WT_SESSION_GONE 破棄 (Section 6 の MUST) を誰が担うかも設計に含める
  - どちらの経路も、`recv_wt_close_session_cb` 発火時点 (不正 UTF-8 経路) または検知時点 (1024 バイト超過経路) で `session_ids_` から削除済みか否かが異なるため、リセットの送出手段 (QUIC 層への直接リセット要求等) を実装時に決める
- テストを追加する: 送信側の UTF-8 境界トリミング (1024 バイト超・マルチバイト文字)、受信側の不正 UTF-8・1024 バイト超過で H3_MESSAGE_ERROR のリセットが発生すること。Sans-IO 構成 (既存のワイヤ検査テストと同様) で検証する
- 変更内容を CHANGES.md の `## develop` に [FIX] として記載する

## 完了条件

- 1024 バイト超・マルチバイト文字を含むエラーメッセージが UTF-8 文字境界で切り詰められて送出される
- 不正な UTF-8 メッセージの受信で H3_MESSAGE_ERROR のリセットが発生する
- 1024 バイト超過のメッセージ受信で H3_MESSAGE_ERROR のリセットが発生する
- それぞれのテストが追加され通る
