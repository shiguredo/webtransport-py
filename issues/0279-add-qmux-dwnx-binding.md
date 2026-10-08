# QMux (dwnx) の Sans-IO バインディングを追加する

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/change-api-and-add-qmux
- Polished: {YYYY-MM-DD}

## 目的

QMux v1 (draft-ietf-quic-qmux-02) を `webtransport.qmux` として使えるようにする。QMux は TLS/TCP のような双方向バイトストリーム上で、QUIC v1 相当のストリームと多重化を提供するプロトコルである。UDP を通せない経路で QUIC 相当の多重化を行うために必要になる。実装には ngtcp2 と同じ API 設計の C 実装 dwnx を用いる。

## 現状

- `src/` に QMux / dwnx の取り込みは無い。`deps.json` は ngtcp2 / nghttp3 / nghttp2 / aws-lc の 4 つで、`refs/` にも draft-ietf-quic-qmux の写しが無い
- QMux のレコードは varint の長さ + QUIC フレーム列で、バイトストリーム上で自己区切りになる。フレームはレコードを跨がず、DATAGRAM フレームの最大長は `max_record_size` トランスポートパラメータで決まる (draft-ietf-quic-qmux-02 Section 3.2 / Section 5.2 / Section 9.1)
- dwnx は MIT、C11、依存ゼロ。ビルドは autotools のみで CMakeLists.txt を持たず、`--enable-lib-only` で外部ライブラリ無しにライブラリだけをビルドできる
- dwnx の公開ヘッダ `dwnx.h` は `dwnx_conn_client_new` / `dwnx_conn_server_new` / `dwnx_conn_read` / `dwnx_conn_write_record` / `dwnx_conn_writev_stream` / `dwnx_conn_write_connection_close` / `dwnx_conn_handle_expiry` / `dwnx_conn_open_bidi_stream` / `dwnx_conn_open_uni_stream` / `dwnx_conn_shutdown_stream` / `dwnx_conn_get_local_transport_params` / `dwnx_ccerr_*` / `dwnx_strerror` を提供する
- dwnx 0.0.0-DEV は DATAGRAM 拡張を実装していない。公開ヘッダに `max_datagram_frame_size` トランスポートパラメータも DATAGRAM フレームを送出する API も無い。CODEBASE.md の「ngtcp2 / nghttp3 / nghttp2 をフォークしない」方針に従い、上流の対応を待つ
- dwnx は TLS を持たない。`dwnx_conn_client_new` は TLS や ALPN を要求しないため、2 つの `dwnx_conn` をバイト列で直結すれば TLS 無しでパラメータ交換からストリームまでを検証できる

## 設計方針

- 依存の取り込み: `deps.json` に `dwnx` を ref 固定で追加し、`CMakeLists.txt` の ExternalProject でビルドする。autotools のみのため `autoreconf -i` と `./configure --enable-lib-only` と `make` を CONFIGURE_COMMAND / BUILD_COMMAND に置く。`lib/*.c` の直接コンパイルは `version.h` の生成と上流のソース追加への追従が要るため採らない
- バインディングは `src/bindings/qmux.cpp` / `src/bindings/qmux.h` を追加し、`src/bind_webtransport.cpp` から `bind_qmux` を呼び、`webtransport_ext.qmux` として公開する
- Sans-IO API は QUIC 層と対になる形にする。`Config` / `Connection` / `Event` / `EventType` を提供し、`Connection.create_client` / `create_server` / `receive` / `send` / `next_event` / `get_timeout` / `handle_timeout` / `open_stream` / `send_stream_data` / `reset_stream` / `stop_sending` / `close_stream` / `close` を持たせる
- QMux はバイトストリーム型なので、`receive(data: bytes) -> int` (消費バイト数) と `send() -> bytes | None` (1 レコード) とする。UDP の `Packet` と `ReceiveResult` は持たず、`webtransport_ext.http2.Connection` と同じ形にする
- レコード境界をまたぐ受信を dwnx が内部で保持できるかを実装時に確認し、保持しない場合は `receive` の前段に長さを読み切るフレーミングを C++ 側に置く。Python 側に持ち込むと、任意のイベントループから使う Sans-IO の契約が崩れるためである
- エラーは層ごとの例外として `src/webtransport/qmux/exceptions.py` に用意する
- `get_version()` を追加し、dwnx のバージョン文字列を返す。`CMakeLists.txt` の `nanobind_add_stub` に `webtransport_ext/qmux.pyi` を追加し、追跡している型スタブも更新する
- `refs/qmux/` に draft-ietf-quic-qmux-02 を追加し、`THIRD_PARTY_LICENSES.md` に dwnx の MIT ライセンスを追記する
- TLS/TCP 上で動かす高レベル API と HTTP/3 over QMux は対象外とし、別 issue に分ける

## 完了条件

- `from webtransport import qmux` で Sans-IO API を利用できる
- Python のクライアントとサーバーをメモリ上のバイト列で直結し、トランスポートパラメータ交換、双方向ストリーム、単方向ストリーム、フロー制御、接続クローズが動く
- レコードが 1 バイトずつ分割されて届いても同じ結果になる
- タイマー (`get_timeout` / `handle_timeout`) がアイドルタイムアウトを駆動する
- モックやスタブを使わず、2 つの接続同士の実通信で検証している
- `webtransport_ext.qmux` の型スタブが生成され、`make develop` のスタブ差分チェックが通る
- 全テストが通過する

## 解決方法
