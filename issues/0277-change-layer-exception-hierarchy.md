# 層ごとの例外階層とエラーコード enum を用意する

- Created: 2026-10-08
- Completed: 2026-10-08
- Branch: feature/change-api-and-add-qmux
- Polished: {YYYY-MM-DD}

## 目的

`webtransport.quic.exceptions` のように層ごとの例外を用意し、QUIC / HTTP/2 / HTTP/3 / WebTransport のエラーを型とエラーコードで扱えるようにする。現状は接続フェーズの 3 例外しか無く、プロトコルが接続やストリームを閉じた理由 (CONNECTION_CLOSE のトランスポートエラー / アプリケーションエラー、RESET_STREAM、STOP_SENDING、GOAWAY、nghttp3 のエラー) を例外として捕捉できない。

## 現状

- `src/webtransport/exceptions.py` にあるのは `WebTransportConnectError` とその派生 3 クラス (`ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError`) だけである
- これらを送出するのは `src/webtransport/h3/client.py` / `src/webtransport/h2/client.py` / `src/webtransport/http3/client.py` の `connect()` である。`src/webtransport/quic/client.py` と `src/webtransport/http2/client.py` の `connect()` は失敗を `bool` で返し、例外を送出しない
- プロトコルエラーは低レベルの `Event` として通知される。QUIC は `EventType.CONNECTION_CLOSED` / `STREAM_RESET`、HTTP/3 は `ERROR` / `RESET_STREAM` / `STOP_SENDING` / `GO_AWAY`、HTTP/2 は `STREAM_RESET` / `GO_AWAY`、WebTransport over HTTP/3 は `ERROR` / `SESSION_CLOSED` / `SESSION_REJECTED` / `GOAWAY` / `RESET_STREAM` / `STOP_SENDING`、WebTransport over HTTP/2 は加えて `SESSION_DRAINING` を持つ。`quic.Connection` は `error_code` / `reason` / `tls_error` / `tls_alert` プロパティを持つ
- エラーコードの定数は `src/webtransport/http3/constants.py` の `H3_*` 11 個と QPACK の 1 個だけである。QUIC (RFC 9000 Section 20.1)、QUIC の TLS エラー (RFC 9001 Section 4.8)、HTTP/2 (RFC 9113 Section 7)、WebTransport (draft-ietf-webtrans-http3-16 Section 5) のコードは定数化されていない
- 高レベル API は `run()` 中にプロトコルエラーを検知しても例外を出さず、接続を閉じて終了する (`src/webtransport/h3/client.py` の `_close_on_protocol_error`、`src/webtransport/http3/client.py` の `_close_on_h3_error` など)

## 設計方針

- ルート基底を `src/webtransport/exceptions.py` の `WebTransportError` とし、層ごとのサブモジュールを置く (`webtransport/quic/exceptions.py` / `http2/exceptions.py` / `http3/exceptions.py` / `h2/exceptions.py` / `h3/exceptions.py`)
- 各モジュールは層の基底 (`QuicError` / `Http2Error` / `Http3Error` / `WebTransportH2Error` / `WebTransportH3Error`) を持ち、通知経路ごとに例外を分ける。接続クローズ (`ConnectionError`)、アプリケーション由来のクローズ (`ApplicationError`)、ストリームリセット (`StreamError`)、送信停止 (`StopSendingError`)、GOAWAY (`GoAwayError`)、TLS ハンドシェイク (`HandshakeError`)、TLS アラート (`TlsAlertError`)、アイドルタイムアウト (`IdleTimeoutError`)、ステートレスリセット (`StatelessResetError`)、バージョン不一致 (`VersionNegotiationError`)、フロー制御 (`FlowControlError`)、HTTP/3 のクリティカルストリームクローズ (`ClosedCriticalStreamError`)、WebTransport のセッション拒否 (`SessionRejectedError`) とセッションクローズ (`SessionClosedError`)、カプセルプロトコル違反 (`CapsuleProtocolError`)
- 例外は `error_code` / `reason` / `stream_id` / `frame_type` を属性に持つ。`error_code` はワイヤ値の `int` で保持し、比較用に `IntEnum` (`QuicTransportErrorCode` / `QuicCryptoErrorCode` / `Http2ErrorCode` / `Http3ErrorCode` / `WebTransportErrorCode`) を同じモジュールに置く
- 低レベル Sans-IO API は現行どおりイベントと戻り値で通知し、例外化しない。任意のイベントループへ組み込む用途で、例外による制御フローを強制しないためである
- 例外を送出するのは高レベル API の次の 3 経路とする。`connect()` の失敗は原因層の例外 (UDP / TCP の到達不能は `quic` / `http2` の接続クローズ例外、TLS の検証失敗は `quic` / `http2` のハンドシェイク例外)、`run()` がプロトコルエラーで終了するときは該当層の例外、明示的な待機 API (`quic.Client.wait_for_stream_reset` など) の中断は該当層の例外
- `ConnectTimeoutError` は接続試行の期限切れという層に依存しない失敗なので `src/webtransport/exceptions.py` に残す。`ConnectRefusedError` / `HandshakeFailedError` は層の例外へ寄せて廃止する
- 送信 API が「接続が閉じている場合は破棄する」挙動は維持する。接続終了と送信の競合をテストで再現するときに例外処理を強制しないためである
- `src/webtransport/http3/constants.py` の `H3_*` 定数は `http3/exceptions.py` の `Http3ErrorCode` に統合し、`webtransport.http3` からの再エクスポートも `Http3ErrorCode` に一本化する

## 完了条件

- 5 層それぞれのエラーが、その層の例外として捕捉でき、`error_code` からエラーコード enum に到達できる
- 例外の `str()` がエラーコード名と理由を含む
- 高レベル API の送出経路 (connect の失敗 / run の致命終了 / 待機 API の中断) が `skills/webtransport-py/SKILL.md` に明記されている
- モックやスタブを使わず、実通信でエラーを起こして検証している
- 全テストが通過する

## 解決方法

- `src/webtransport/quic/exceptions.py` / `http2` / `http3` / `h2` / `h3` に層ごとの例外とエラーコードの `IntEnum` を追加した。基底は `webtransport.exceptions.WebTransportError` で、接続の期限切れを表す `ConnectTimeoutError` と、名前解決の失敗のような接続前の失敗を表す `ConnectFailedError` はトップレベルに置いた
- 旧 `WebTransportConnectError` / `ConnectRefusedError` / `HandshakeFailedError` を廃止し、`h3.Client.connect()` / `h2.Client.connect()` / `http3.Client.connect()` が原因となった層の例外 (`QuicConnectionError` / `QuicHandshakeError` / `Http2ConnectionError` / `Http2HandshakeError` / `WebTransportProtocolError` / `WebTransportSessionRejectedError` / `WebTransportSessionClosedError`) を送出するようにした
- `quic.Client.run()` / `http3.Client.run()` / `h3.Client.run()` / `h2.Client.run()` が、接続またはセッションがエラーで終了したときにその原因の例外を送出して終了するようにした。正常終了 (NO_ERROR のクローズ・アイドルタイムアウト・ローカルからの `close()`) では送出しない
- `src/bindings/quic.cpp` / `src/bindings/quic.h` に `error_code_type` と `error_frame_type` を追加し、受信した CONNECTION_CLOSE の種別 (transport / application) と原因フレームを取れるようにした。`QuicTransportError` と `QuicApplicationError` はこれで作り分ける
- `src/webtransport/http3/constants.py` の `H3_*` 定数を `Http3ErrorCode` に統合し、`webtransport.http3.constants` を削除した
- 設計方針に挙げた例外のうち、観測できないものはクラスとして定義しなかった。ストリーム単位のエラー (RESET_STREAM / STOP_SENDING) と GOAWAY は低レベルのイベントと `on_stream_reset` / `on_goaway` で通知する (接続を終わらせず同じ接続で回復できるため)。ステートレスリセット・バージョン不一致・アイドルタイムアウトは ngtcp2 の ccerr から区別できないため、`QuicConnectionError` の理由とイベントで表す
- `tests/test_exceptions.py` を追加し、基底関係・エラーコードの値 (RFC / draft と照合)・属性・`str()`・CONNECTION_CLOSE の有無による種別の切り替えを検証した。既存テストは connect 失敗とプロトコルエラーによる `run()` 終了の期待値を新しい例外へ移行した
- `skills/webtransport-py/SKILL.md` に「例外」節を追加し、階層・エラーコード・属性・送出経路を明記した
