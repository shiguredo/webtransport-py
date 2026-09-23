# quic 層でサーバーがストリームを中断し接続を終了コードと理由付きで閉じられるようにする

- Created: 2026-09-23
- Completed: {YYYY-MM-DD}
- Branch: feature/add-quic-server-stream-and-connection-close
- Polished: {YYYY-MM-DD}

## 目的

quic 層で、サーバーがクライアント単位のストリーム中断 (RESET_STREAM / STOP_SENDING) と、終了コードと理由付きの接続終了 (CONNECTION_CLOSE) を行えるようにする。あわせてピアの RESET_STREAM を受信側で観測できるようにする。

低レベル `webtransport_ext.quic.Connection` は `close_stream` (RESET_STREAM + STOP_SENDING) / `stop_sending` / `reset_stream` / `close(error_code, reason)` を持つが、高レベル `webtransport.quic` の `Server` はこのいずれも公開していない。サーバー側でできるのは `open_stream` / `send_stream_data` / `send_datagram` / `initiate_key_update` と、全接続を閉じる `stop()` だけである。

このため、QUIC 直結でプロトコルのサーバー役を実装するアプリは、ストリームの途中中断もセッション終了の通知もできない。peer は接続終了を検知できず期限まで待ち続ける。moqt-py は `Transport.Quic` の E2E テストをこの理由で保留している。

`quic.Client.close()` も引数を取らないため、アプリは終了コードと理由を渡せない。CONNECTION_CLOSE のアプリケーションエラーコードと理由は `ngtcp2_ccerr_set_application_error` で表現でき、低レベル層は対応済みである。

## 現状

- `src/webtransport/quic/server.py` の `Server` に `shutdown_stream` / `reset_stream` / `close_stream` / `stop_sending` が無い。`close` も無く、`stop()` が全接続を `Connection.close()` で閉じたうえでサーバーを停止するだけである
- `src/webtransport/quic/server.py` の `Server` は `on_handshake_completed` / `on_stream_data` / `on_datagram` / `on_stop_sending` / `on_connection_closed` を持つが、ピアの RESET_STREAM を通知するコールバックが無い。`quic.Client` は `wait_for_stream_reset` でピアの RESET_STREAM を待てるのに対し、サーバー側には相当する観測手段が無い
- `src/webtransport/quic/client.py` の `Client.close()` は引数を取らず、低レベル `Connection.close()` を既定の error code 0 / 空の理由で呼ぶ。`quic.Client.shutdown_stream(stream_id, error_code=0)` は RESET_STREAM と STOP_SENDING を送出するが、STOP_SENDING だけを送る API は無い (moqt-py は `shutdown_stream` で代用している)
- `src/webtransport/webtransport_ext/quic.pyi` の宣言は `close_stream(stream_id, error_code=0)` / `stop_sending(stream_id, error_code=0)` / `reset_stream(stream_id, error_code=0)` / `close(error_code=0, reason="")` である
- ハンドシェイク完了前の close は ngtcp2 がエラーコードを APPLICATION_ERROR に置換して理由を落とす (RFC 9000 Section 10.2.3)。終了コードと理由が伝わるのはハンドシェイク完了後である
- 0220 が `quic.Server.shutdown_stream(addr, stream_id, error_code=0)` と `quic.Server.close(addr, error_code=0, reason="")` の追加を対象にしているが、実装はまだ develop に入っていない。0220 の完了条件も「`shutdown_stream` が RESET_STREAM と STOP_SENDING を送出する」「`close` が指定したクライアントへ終了コードと理由付きの CONNECTION_CLOSE を送出する」までであり、本 issue の `stop_sending` / `reset_stream` / `on_stream_reset` は対象外である
- 影響: MOQT のサーバー役を QUIC 直結で実装するアプリは、購読単位の cancel とセッション終了の双方を peer へ伝えられない (moqt-py の `moqt.moq.testing.server.Server` は `Transport.Quic` で `ValueError` を送出して保留している)

## 設計方針

- **接続単位のストリーム操作を追加する**: `quic.Server.shutdown_stream(addr, stream_id, error_code=0)` / `reset_stream(addr, stream_id, error_code=0)` / `stop_sending(addr, stream_id, error_code=0)` を追加し、`self._connections` から `addr` で接続を引いて低レベル `Connection` の同名メソッドを呼び、`_send_to` で送出する。引数の形はサーバー側の既存 API (`send_stream_data` / `send_datagram`) に揃える。`shutdown_stream` の意味は `quic.Client.shutdown_stream` と同じにする (双方向ストリームでは RESET_STREAM と STOP_SENDING の両方、単方向ストリームでは ngtcp2 の `ngtcp2_conn_shutdown_stream` が決める側だけを shutdown する)。`reset_stream` と `stop_sending` は片方だけを送る操作であり、`h2` 層の同名 API がセッションを閉じ込めた層の操作であるのに対し、本層は特定の接続のストリームを指定する QUIC フレーム層の操作である点を docstring に書く
- **`quic.Server.close(addr, error_code=0, reason="")` を追加する**: `self._connections` から `addr` で接続を引いて低レベル `Connection.close(error_code, reason)` を呼び、`_send_to` で CONNECTION_CLOSE を送出する。未登録の `addr` では何もしない (`send_datagram` と同じ扱い)。ローカル起点の終了として扱い `on_connection_closed` は発火させない (`quic.Client.close()` と同じ契約)。接続の後始末は受信ループの回収経路に任せ、`stop()` を呼ばずに 1 接続だけを閉じられるようにする。0220 が扱う「アプリコールバックの中から `close` を呼んでもコールバックを `CancelledError` で中断しない」回収経路の修正は 0220 の成果を取り込む前提とし、本 issue では重複して設計しない
- **ピアの RESET_STREAM を通知する**: `quic.Server.on_stream_reset(callback)` を追加し、`callback(stream_id: int, error_code: int, addr: tuple[str, int])` の形にする。`on_stop_sending` と同じ引数の形であり、低レベル `Connection.next_event()` の `STREAM_RESET` から転送する。既存の `on_stop_sending` の分岐と同じ場所で扱う
- **`quic.Client.close(error_code=0, reason="")` に任意引数を追加する**: 低レベル `Connection.close(error_code, reason)` へそのまま渡す。送出の手順・戻り値 (送出できたパケット数)・後始末は変えない。引数なしの `close()` が従来どおり動くことを既存テストで維持する
- **対象外**: ハンドシェイク完了前に終了コードと理由を伝えること (ngtcp2 と RFC 9000 Section 10.2.3 の制約)、レスポンスの `error_code` を `receive` の戻り値へ載せる変更、`quic.Server` の `wait_for_stream_reset` 相当 (クライアント専用の受信待機であり、サーバーは `on_stream_reset` で観測する)
- **変更対象**: `src/webtransport/quic/server.py`, `src/webtransport/quic/client.py`、`skills/webtransport-py/SKILL.md` (quic 節)、追加 API のテスト (Sans-IO のピアでフレームを観測する既存の検証方法に揃える)

## 完了条件

- `quic.Server.shutdown_stream(addr, stream_id, error_code)` が RESET_STREAM と STOP_SENDING を、`reset_stream` が RESET_STREAM を、`stop_sending` が STOP_SENDING を送出する。ピア側でそれぞれのフレームとエラーコードが観測できる
- `quic.Server.close(addr, error_code, reason)` が指定したクライアントへ終了コードと理由付きの CONNECTION_CLOSE を送出し、ピア側でその終了コードと理由が観測できる。未登録の `addr` では何も起きず、`on_connection_closed` は発火しない。`stop()` を呼ばずに 1 接続だけを閉じられ、他クライアントの通信は継続する
- `quic.Server.on_stream_reset` がピアの RESET_STREAM で 1 回発火し、ストリーム ID とアプリケーションエラーコードを渡す。未登録の場合は従来どおり通知しない
- `quic.Client.close(error_code, reason)` が指定した終了コードと理由をピアへ伝え、引数省略時の挙動は変わらない
- 追加分のテストを入れる。`shutdown_stream` / `reset_stream` / `stop_sending` は送出するフレームまで、`close` はピア側の `error_code` / `reason` で終了コードと理由が観測できるところまで検証する。ハンドシェイク完了前の close で理由が伝わらない既知の制約もテストの前提として明示する
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- `skills/webtransport-py/SKILL.md` の quic 節を追加 API に合わせて更新する
- 全テストが通過する

## 解決方法
