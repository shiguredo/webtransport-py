# QMux のローカル発ストリーム終了 API を追加する

- Created: 2026-10-14
- Completed: 2026-10-15
- Branch: feature/add-qmux-stream-shutdown-apis
- Polished: {YYYY-MM-DD}
- Reporter: @voluntas

## 目的

QMux の Sans-IO API には、ローカルから RESET_STREAM / STOP_SENDING を送る経路が無い。ピア発の RESET_STREAM / STOP_SENDING は `EventType.STREAM_RESET` / `EventType.STOP_SENDING` としてアプリに届くのに、逆向きの送信ができないという片肺の状態になっている。

0279 の設計方針は `Connection` に `reset_stream` / `stop_sending` / `close_stream` を持たせると定めていたが、解決方法で実装されたのは `open_stream` / `send_stream_data` / `close` だけであり、0279 の積み残しである。draft-ietf-quic-qmux-02 Section 4 が RESET_STREAM と STOP_SENDING を QMux で使えるフレームとして挙げているとおりプロトコル上は実装可能で、必要な dwnx の API は `deps.json` の ref に固定済みの版に既に存在する。上流待ちではなく webtransport-py 側の穴である。

## 現状

- `src/bindings/qmux.h` の `QmuxConnection` が公開するのは `open_stream` / `send_stream_data` / `close` と Sans-IO の入出力 (`receive` / `pending_record` / `get_timeout` / `handle_timeout` / `next_event`) である。`send_stream_data` の `fin` は `DWNX_WRITE_STREAM_FLAG_FIN` による write 側の graceful クローズだけで、`src/bindings/qmux.cpp` は `dwnx_conn_shutdown_stream` / `dwnx_conn_shutdown_stream_write` / `dwnx_conn_shutdown_stream_read` を一度も呼んでいない
- ピア発の RESET_STREAM / STOP_SENDING は `stream_reset_cb` / `recv_stop_sending_cb` が `QmuxEventType::StreamReset` / `QmuxEventType::StopSending` に変換し、`EventType.STREAM_RESET` / `EventType.STOP_SENDING` として Python に届く。受信経路だけがつながっている
- asyncio 層 (`src/webtransport/qmux/client.py` / `server.py` / `_connection.py`) は `on_stream_reset` / `on_stop_sending` コールバックを持つが、`Client` と `Server` の `Session` にローカルから送るメソッドは無い。`webtransport.h2` / `webtransport.h3` の `Session` は `reset_stream` / `stop_sending` を持つため、統一 API としても非対称である
- `skills/webtransport-py/SKILL.md` には「ストリームのリセットと送信停止 (reset_stream / stop_sending) は Sans-IO のバインディングが未対応のため asyncio API にも無い」と書かれている
- dwnx (`deps.json` の ref `85c43b506e48f268029e98514d26fe65c8ff749e`) は `dwnx_conn_shutdown_stream_write` (write 側の即時クローズで RESET_STREAM を送出)、`dwnx_conn_shutdown_stream_read` (read 側の即時クローズで STOP_SENDING を送出)、`dwnx_conn_shutdown_stream` (両方向の即時クローズ) を公開済みである。対象のストリームが存在しない場合は 0 を返し、ローカルの単方向ストリームへの write 側 shutdown とリモートの単方向ストリームへの read 側 shutdown は `DWNX_ERR_INVALID_ARGUMENT` を返す
- DATAGRAM は dwnx 側が未実装であり、本 issue の対象外である

## 設計方針

- `QmuxConnection` に `reset_stream(stream_id, error_code)` / `stop_sending(stream_id, error_code)` / `close_stream(stream_id, error_code)` を追加し、それぞれ `dwnx_conn_shutdown_stream_write` / `dwnx_conn_shutdown_stream_read` / `dwnx_conn_shutdown_stream` を呼ぶ。dwnx は shutdown で予約したフレームを後続の `dwnx_conn_writev_stream` / `dwnx_conn_write_record` が送出するため、`send_stream_data` のような `PendingStreamData` のキューは通さず、呼び出し時に dwnx へ渡す
- `close_stream` の名前と意味は h3 と揃えるかを決める。`src/bindings/http3.h` の `close_stream` は write 側を閉じる graceful な終了 (FIN) だが、dwnx の `dwnx_conn_shutdown_stream` は両方向の即時クローズである。QMux に HTTP/3 の意味論は無いため、両方向の即時クローズを `close_stream` として公開し `send_stream_data(fin=True)` を graceful クローズと位置付ける案と、`shutdown_stream` という別名にする案を比較して決める
- 引数の誤り (単方向ストリームの向き違い) をどう見せるかを 1 つに決める。`open_stream` は失敗を -1 で返し、`receive` は dwnx のライブラリエラーコードをそのまま返している。向き違いはプログラミングエラーなので Python 例外が自然だが、既存の 2 経路との一貫性を優先して決める
- asyncio 層の `Client` と `Server` の `Session` に `reset_stream` / `stop_sending` を追加し、`ConnectionDriver` の書き込み経路 (既存の `open_stream` / `send_stream_data` と同じ `asyncio.Lock` とフラッシュ) に載せる。引数は h2 / h3 の `Session` と同じ `(stream_id, error_code=0)` にする
- エラーは層ごとの例外階層に合わせ、`webtransport.qmux.exceptions` の既存の型で表現する
- `CMakeLists.txt` の型スタブ (`webtransport_ext/qmux.pyi`) と `skills/webtransport-py/SKILL.md` の該当記述を更新する
- テストはモックやスタブを使わない。Sans-IO は 2 つの `Connection` をメモリ上のバイト列で直結し、`reset_stream` がピアに `EventType.STREAM_RESET` として、`stop_sending` が `EventType.STOP_SENDING` として届き、終了コードがイベントに載ることを検証する。asyncio は実 TCP で検証する

## 完了条件

- Sans-IO API と asyncio API の両方からローカルの reset と stop_sending を送れる
- ピア側に `STREAM_RESET` / `STOP_SENDING` イベントとして届き、asyncio では `on_stream_reset` / `on_stop_sending` が呼ばれる
- 両方向の即時クローズに相当する API が追加され、h3 の `close_stream` との意味の差が SKILL.md に明記されている
- モックやスタブを使わず、実通信のテストで検証している
- `make develop` の型スタブ差分チェックと全テストが通る

## 解決方法

- `src/bindings/qmux.h` / `.cpp` の `QmuxConnection` に `reset_stream(stream_id, error_code=0)` / `stop_sending(stream_id, error_code=0)` / `close_stream(stream_id, error_code=0)` を追加した。それぞれ `dwnx_conn_shutdown_stream_write` (RESET_STREAM) / `dwnx_conn_shutdown_stream_read` (STOP_SENDING) / `dwnx_conn_shutdown_stream` (両方向の即時終了) を呼ぶ。予約されたフレームは既存の `pending_record` (`send()`) が送出するため、`send_stream_data` のような `PendingStreamData` のキューは通さない。戻り値は dwnx のライブラリエラーコードで、0 以上は成功、負値はエラーである (存在しないストリームは dwnx が 0 を返すため黙って無視される)
- 名前と意味は `webtransport_ext.quic.Connection` (`close_stream` = RESET_STREAM + STOP_SENDING) に揃えた。h3 層の `close_stream` (HTTP/3 のエラーコード空間でストリームを終了する) とは意味もエラーコードの空間も異なることを docstring と SKILL.md に明記し、QMux の graceful な書き込み側の終了は `send_stream_data(fin=True)` と位置付けた。`shutdown_stream` という別名は採らず、低レベル (`webtransport_ext.quic.Connection`) と層内で名前を揃えることを優先した
- `src/webtransport/qmux/_connection.py` の `ConnectionDriver` に同じ 3 メソッドを追加し、`asyncio.Lock` と `_flush_locked()` で既存の送信経路に載せた。dwnx のライブラリエラーは `_raise_on_library_error` で `QmuxProtocolError` に変換する (0 以上と CLOSING / DRAINING / IDLE_CLOSE は成功扱い)。この判定は `_pump_once` の receive エラー処理と共通化した
- `src/webtransport/qmux/client.py` の `Client` と `src/webtransport/qmux/server.py` の `Session` に同名メソッドを追加した (未接続の `Client` は既存の `_require_driver()` が RuntimeError を送出する)
- `src/webtransport/webtransport_ext/qmux.pyi` は `make develop` の生成結果に合わせた
- テストはモックやスタブを使わず、Sans-IO は 2 つの `Connection` をバイト列で直結し、asyncio は実 TCP で検証した。`tests/test_qmux.py` に 5 件 (STREAM_RESET / STOP_SENDING の通知、`close_stream` が両方向を送ること、単方向ストリームの向き違いが DWNX_ERR_INVALID_ARGUMENT になること、存在しないストリームが no-op になること)、`tests/test_qmux_asyncio.py` に 4 件 (クライアント→サーバーの `stop_sending`、サーバー→クライアントの `reset_stream`、`close_stream` が両方向として観測できること、向き違いが `QmuxProtocolError` になること) を追加した
- RED は 2 段階で実測した。バインディング未実装では Sans-IO の 5 件が AttributeError で失敗し、実装後に `ConnectionDriver` の 3 メソッドを外すと asyncio の 4 件が AttributeError で失敗する
- `skills/webtransport-py/SKILL.md` の Sans-IO 節と asyncio 節を更新し、「バインディングが未対応のため asyncio API にも無い」という記述を削除した
- `CHANGES.md` は CODEBASE.md の指示により更新しない
- 全テストが通過する (1483 passed。prek の全フックも通過)
