# 変更履歴

- CHANGE
  - 後方互換性のない変更
- ADD
  - 後方互換性がある追加
- UPDATE
  - 後方互換性がある変更
- FIX
  - バグ修正

## develop

- [CHANGE] Windows 対応を終了する
  - @voluntas
- [CHANGE] `http3.Connection.goaway` の無視されていた `id` 引数を削除する
  - @voluntas
- [CHANGE] `http3.Connection.close_stream` の既定 `error_code` を H3_NO_ERROR (0x0100) に変更する
  - @voluntas
- [CHANGE] `http2.EventType` と `http3.EventType` に `INFORMATIONAL` (1xx) と `TRAILERS` を追加し、受信トレーラと 1xx を `HEADERS` から区別できるようにする
  - @voluntas
- [CHANGE] WebTransport over HTTP/2 の `h2.Session.reset_stream` から `reliable_size` 引数を廃止し、Reliable Size に送信済みバイト数を載せるようにする
  - @voluntas
- [CHANGE] WebTransport over HTTP/2 の `h2.Client` は TLS 1.3 以上で接続し、`h2.Server` は TLS 1.2 以下の接続を拒否するようにする (draft-15 Section 7 準拠)
  - @voluntas
- [CHANGE] HTTP/2 のリクエスト終端を `send_data(..., eof=True)` に変更し、`http2.Client.request` に `body` を追加する
  - @voluntas
- [CHANGE] QUIC の `receive` / `send` / `create_client` / `accept` に実アドレスを必須化する
  - @voluntas
- [CHANGE] WebTransport over HTTP/3 と HTTP/2 の `Client.connect` を例外送出型 (`connect(timeout) -> None`) に変更し、`bool` 戻り値を廃止する
  - @voluntas
- [CHANGE] WebTransport over HTTP/3 の `on_stream_reset` の `error_code` を `int | None` に変更する (レンジ外・予約済みコードポイントは `None`)
  - @voluntas
- [ADD] WebTransport over HTTP/2 を draft-ietf-webtrans-http2-15 に合わせて実装する
  - @voluntas
- [ADD] WebTransport over HTTP/3 を draft-ietf-webtrans-http3-16 に合わせて実装する
  - @voluntas
- [ADD] QUIC (ngtcp2) のバインディングを実装する
  - @voluntas
- [ADD] HTTP/3 (nghttp3) のバインディングを実装する
  - @voluntas
- [ADD] HTTP/2 (nghttp2) のバインディングを実装する
  - @voluntas
- [ADD] QUIC / HTTP/2 / HTTP/3 / WebTransport over HTTP/3 / WebTransport over HTTP/2 の高レベル asyncio Client / Server を実装する
  - @voluntas
- [ADD] WebTransport の `connect()` 失敗を通知する例外階層 (`WebTransportConnectError` / `ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError`) を追加する
  - @voluntas
- [ADD] QUIC の 0-RTT による early data 送受信と Session ticket を実装する
  - @voluntas
- [ADD] QUIC の Connection Migration を実装し、`quic.Client` / `h3.Client` / `http3.Client` に `migrate()` を追加する。`h3.Server` / `http3.Server` は Connection Migration を受け付ける
  - @voluntas
- [ADD] QUIC クライアントの証明書検証 (`ca_file` とカスタム検証コールバック) を実装する
  - @voluntas
- [ADD] QUIC の接続統計 API (RTT / 輻輳ウィンドウ / フロー制御残量 / 送受信量等) と接続状態・エラー・ピア情報 API (コネクションエラー / TLS エラー / トランスポートパラメータ / バージョン / 接続 ID 等) を公開する
  - @voluntas
- [ADD] QUIC のストリーム・接続制御 API (ストリーム上限確認 / keep-alive / 鍵更新 / フロー制御の動的拡張 / 単方向・双方向ストリームの開設) を公開する
  - @voluntas
- [ADD] QUIC クライアントに `recv_stream_data` / `shutdown_stream` / `wait_for_stream_reset` / `discard_recv_state` を追加し、バックグラウンド受信タスクで `run()` を明示起動しなくても受信イベントを処理できるようにする
  - @voluntas
- [ADD] `quic` / `h3` / `http3` の高レベル Client / Server に `initiate_key_update()` を追加する
  - @voluntas
- [ADD] HTTP/2 のメッセージング拡張 API (トレーラ送信 / 1xx 応答 / RFC 9218 の優先度制御 / Server Push / ALPN 選択 / PING の opaque data / WINDOW_UPDATE の増分値) を公開する
  - @voluntas
- [ADD] HTTP/2 のセッション状態確認 API (SETTINGS / ウィンドウサイズ / 送信キュー / half-closed 状態等) とセッション制御 API (GOAWAY による即時終了 / ローカルウィンドウサイズの動的変更) を公開する
  - @voluntas
- [ADD] HTTP/3 の送信側拡張 API (トレーラ / 1xx レスポンス / graceful shutdown の開始通知 / 書き込み側シャットダウン) と優先度制御 API (RFC 9218) を公開する
  - @voluntas
- [ADD] HTTP/3 と WebTransport over HTTP/3 のストリーム・接続制御 API (QUIC フロー制御ブロック / アンブロック / 同時ストリーム数ヒント) を公開する
  - @voluntas
- [ADD] WebTransport over HTTP/3 のサーバーにストリームを開く API と Origin ヘッダーの送信・検証を追加する
  - @voluntas
- [ADD] WebTransport over HTTP/2 の `h2.Server` に `allowed_origins` とセッション拒否 API (`on_session_request`) を追加し、Origin 検証 (draft-15 Section 3.2) を実装する
  - @voluntas
- [ADD] WebTransport over HTTP/2 の `h2.Client` / `h2.Server` に `on_stop_sending` を追加し、`stop_sending` と WT_STOP_SENDING への WT_RESET_STREAM 自動応答を実装する
  - @voluntas
- [ADD] `http3.EventType.ERROR` と `h3.EventType` / `http2.EventType` のエラー通知、`on_connection_error` / `on_error` / `on_stop_sending` / `on_stream_reset` コールバックを追加する
  - @voluntas
- [ADD] `h2.WtErrorCode` を追加し、WebTransport over HTTP/2 のエラーコード (`WT_FLOW_CONTROL_ERROR` / `WT_STREAM_STATE_ERROR` / `WT_ERROR`) を定数として公開する
  - @voluntas
- [ADD] `http2.Server` と `http3.Server` にリクエストボディ終端 (`on_stream_end`) コールバックを追加する
  - @voluntas
- [ADD] 配布 wheel に THIRD_PARTY_LICENSES.md を同梱する
  - @voluntas
- [UPDATE] UDP 系の高レベル API (`quic` / `h3` / `http3` の Client / Server) の送信を `send()` が空を返すまで drain し、待機を QUIC のタイマー期限ベースに変更して大容量転送のスループットを改善する
  - @voluntas
- [UPDATE] `http2.Server` の送信を drain 化し、受信を常時読み待ちにして大容量レスポンスのスループットを改善する
  - @voluntas
- [UPDATE] WebTransport over HTTP/2 の受信をアプリの消費に連動させ (`nghttp2_session_consume`)、未完成カプセルの保持バイト数を有界にする
  - @voluntas
- [UPDATE] CI の対応プラットフォームを Ubuntu 24.04 LTS / macOS 26 に揃える
  - @voluntas
- [UPDATE] 依存ライブラリ (ngtcp2 / nghttp3 / nghttp2 / AWS-LC / nanobind) を固定したコミット・タグでビルドする
  - @voluntas
- [UPDATE] QUIC の高レベル API (`quic` の Client / Server) の受信を 1 回の待機で複数パケットまとめて取り込み、待機を次の QUIC タイマー期限に委ねて大容量転送のスループットを改善する
  - @voluntas
- [UPDATE] WebTransport over HTTP/3 / HTTP/3 の高レベル API (`h3` / `http3` の Client / Server) で、受信のたびに挟んでいた固定 sleep を廃止し、複数パケットのまとめ取りとタイマー期限ベースの待機に揃えて大容量転送のスループットを改善する
  - @voluntas
- [UPDATE] WebTransport over HTTP/2 / HTTP/2 の高レベル API (`h2` / `http2` の Client / Server) で、受信のたびに挟んでいた固定 sleep を廃止して大容量転送のスループットを改善する
  - @voluntas
- [FIX] WebTransport over HTTP/3 と HTTP/3 のサーバーが、QUIC タイマーが遠い状態でアプリが積んだ送信 (ストリーム解放の RESET_STREAM 等) を次にパケットが届くまで送出しない問題を修正する
  - @voluntas
- [FIX] HTTP/2 の送信バッファで先頭 1 バイトが欠落する問題と、空のボディで END_STREAM が送出されない問題を修正する
  - @voluntas
- [FIX] QUIC の `send()` が輻輳ウィンドウ枯渇時に無限ループする問題、ngtcp2 の WRITE_MORE 契約違反で大容量データ転送が壊れる問題、再送時にストリームデータが破損する問題を修正する
  - @voluntas
- [FIX] QUIC の受信フロー制御が初期受信ウィンドウを超えて前進しない問題と、サーバーが単一ループのため他接続の受信と再送が止まる問題を修正する
  - @voluntas
- [FIX] WebTransport over HTTP/3 と HTTP/2 のストリーム終了後の送出・送信バッファ解放・セッション終了の後始末が不正な問題を修正する
  - @voluntas
- [FIX] WebTransport over HTTP/3 と HTTP/2 の受信で入力検証・上限・フロー制御違反の検知が漏れていた問題を修正する (カプセルサイズ / 入力サイズ / ストリーム数 / フロー制御値の減少・逆転)
  - @voluntas
- [FIX] WebTransport over HTTP/3 と HTTP/2 の `close_session` がエラーメッセージの UTF-8 文字境界を無視して切り詰める問題と、不正なメッセージを受信してもセッションエラーにしない問題を修正する
  - @voluntas
- [FIX] WebTransport over HTTP/3 の `Client.connect` が SETTINGS の受信判定を誤りハンドシェイク完了を取りこぼす問題と、`Client.connect()` の待機が無制限になる問題を修正する
  - @voluntas
- [FIX] WebTransport over HTTP/3 と HTTP/2 の `Client.connect()` が非 2xx 応答や無応答で永久にブロックする問題を修正する
  - @voluntas
- [FIX] WebTransport over HTTP/3 と HTTP/2 のサーバーが非 WebTransport リクエストへ無応答のままストリームを滞留させる問題を修正する (405 + Allow: CONNECT)
  - @voluntas
- [FIX] WebTransport over HTTP/3 の QPACK デコードブロック中の CONNECT ストリームに DATA フレームが後続するとサーバーが異常終了する問題を修正する
  - @voluntas
- [FIX] QUIC の `close()` が生成した CONNECTION_CLOSE パケットを送出しない問題と、close 後の受信パケットへの応答で CONNECTION_CLOSE を再送し続ける問題を修正する
  - @voluntas

### misc

- [UPDATE] 高レベル API と C++ バインディングの重複コードを共通ヘルパーへ集約する (`_common.py` と `header_convert.h` の新設)
  - @voluntas

- [UPDATE] 全 C コールバックに `noexcept` を付与し、例外境界の方針を CODEBASE.md に明記する
  - @voluntas

- [UPDATE] 型スタブをスタブパッケージ化し、高レベル `Client` / `Server` / 例外を型検査に露出する
  - @voluntas

- [UPDATE] ruff の検出ルールを `select` で明示的に固定し、ローカルの ruff を prek.toml と同じバージョンに固定する
  - @voluntas

- [UPDATE] print 駆動のデバッグテスト 3 本を削除し、テストのイベント取り出し・接続ペア・ワイヤ組み立てヘルパーを conftest.py に集約する
  - @voluntas

- [UPDATE] 確立済みペアへの API 呼び出し系列を検証するステートフル PBT と、テスト専用の観測 API (`_test_force_close` / `_test_stream_buffer_*`) を追加する
  - @voluntas

- [UPDATE] テスト用の UDP パケットロス注入リレー LossyRelay とハンドシェイクロスからの回復テストを追加する
  - @voluntas

- [UPDATE] CI のフレーク (QUIC の pacing 依存 / 接続確立 / 100 並行接続) を修正し、wheel テストでチェックアウトの src が wheel を隠さないようにする
  - @voluntas

- [UPDATE] C++ バインディングの死にコード削除・RuntimeError メッセージの英語統一・Windows 対応終了後の残骸削除・依存の deprecated API 移行を行う
  - @voluntas

- [UPDATE] CI に ruff check / ty check を追加し、wheel ワークフローの外部 action をコミットハッシュ固定に統一する
  - @voluntas
