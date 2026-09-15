# 重複実装を共通ヘルパーへ集約する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-consolidate-duplicated-code
- Polished: 2026-09-15

## 目的

同一実装が複数箇所に存在し、仕様変更やバグ修正の影響範囲が広がっている。共通ヘルパーへ集約して保守性を高める。closed/0125 で URL パース・ヘッダー変換・セッション終了ガードなどの集約は完了しており、本 issue は 0125 が対象外とした残件のうち、下記の重複を扱う。

## 現状

C++ 側:

- `is_valid_utf8` が `src/bindings/webtransport_h2.cpp` と `src/bindings/webtransport_h3.cpp` に完全一致の実装で存在する (直前のコメントは層ごとに異なる)
- 手書きの QUIC 可変長整数の実装が 2 ファイルに 4 関数ある。`src/bindings/quic.cpp` の `quic_varint_length` (長さ計算) と `decode_varint`、`src/bindings/webtransport_h2.cpp` の `H2Session::encode_varint` と `H2Session::decode_varint` が同一仕様 (RFC 9000 Section 16) を別実装しており、境界値の定数も書き分けられている (`<= 63` / `<= 16383` / `<= 1073741823` と `< 64` / `< 16384` / `< 1073741824`)。エラー契約も異なる (`quic.cpp` の `decode_varint` は失敗時に 0 を返して `consumed` に 0 を設定し、`H2Session::decode_varint` は `std::nullopt` を返す)。`quic.cpp` にエンコード実装は無く、実際のエンコードは ngtcp2 が行う。h3 側は `nghttp3_get_uvarint` / `nghttp3_put_uvarint` を使うため重複ではない
- WT_APPLICATION_ERROR の順方向写像が C++ と Python に二重実装されている。`src/bindings/webtransport_h3.cpp` の `webtransport_code_to_http_code` と `src/webtransport/h3/_error_codes.py` が同じ式と同じ魔法数 (`0x52e4a40fa8db` / `0xffffffff` / `0x1e`) を持つ。片側だけ改訂されるとワイヤ値が食い違う。C++ 側は順方向のみで無名 namespace 内にあり、Python からは呼べない。Python 側は順方向と逆方向の両方を持ち、順方向はテストのオラクルとして使われている (`tests/test_webtransport_h3_error_code_remap.py` の `assert session.map_send_error_code(4, 0x01) == webtransport_code_to_http_code(0x01)`)

Python 側:

- `_timeout_seconds` が 6 箇所にある。クライアント 3 箇所 (`quic/client.py` / `h3/client.py` / `http3/client.py`) は単一接続の期限計算でほぼ同一だが、`quic/client.py` だけ `timeout_ns <= 0` で 0.0 を返す分岐を持つ。サーバー 3 箇所 (`quic/server.py` / `h3/server.py` / `http3/server.py`) は接続集合を走査して最小期限を求める形で、走査対象が `_conn_addr` と `_clients` で異なる
- `_send_pending` が 5 箇所にあるが 3 形態ある。`quic/client.py` / `h3/client.py` / `http3/client.py` はソケットへ `loop.sock_sendto` で送るループ (戻り値 `int`)、`http2/client.py` は `asyncio` の `StreamWriter` へ write / drain するループ (戻り値 `None`)、`h2/client.py` は同じく `StreamWriter` への単発書き込みでループを持たない (戻り値 `None`)
- `_resolve_remote` が `src/webtransport/quic/client.py` / `h3/client.py` / `http3/client.py` の 3 箇所に同一実装で存在する
- DCID 索引を扱う `_refresh_dcid_index` と `_drop_dcid_index` が `quic/server.py` / `h3/server.py` / `http3/server.py` の 3 箇所に重複している。`_addr_of` は h3 / http3 の 2 箇所のみで、`quic/server.py` は `_conn_addr` による逆引きで同じ役割を担う (データ構造も計算量も異なる)
- `_receive` は 5 箇所に残る (`h2` / `h3` / `http2` / `http3` / `quic` の client)。0209 が集約したのは受信待ち (`src/webtransport/_common.py` の `recv_datagram`) であり、読み切るループの本体は集約されていない。とくに `h3/client.py` と `http3/client.py` の `_receive` は参照する接続属性以外が同一である。`_receive` 本体の集約は本 issue の対象外とする

テスト側:

- `http3.Connection` 用の `_pump` が `tests/test_http3.py` / `test_http3_ack_offset.py` / `test_http3_message_ext.py` / `test_http3_stream_control.py` / `test_http3_stream_priority.py` / `test_http3_stream_state.py` の 6 ファイルに同一実装で存在する (docstring のみ差)。`tests/conftest.py` の `_pump` は `h3.Session` 専用のため流用できていない
- `_create_connection_pair` が `test_http3.py` を除く 5 ファイルに存在し、`test_http3_stream_priority.py` だけが `server.set_max_client_streams_bidi(100)` を追加で呼ぶ (同ファイルの減算無視テストがこの値に依存する)
- 自己署名証明書の生成が `tests/conftest.py` に 2 つある。`test_certificates` フィクスチャは subject 5 属性と SAN (`DNSName("localhost")` と `IPAddress(127.0.0.1)`) を持ち `tempfile.TemporaryDirectory()` で後始末する。`create_test_certificates` は `CN=localhost` のみで SAN を持たず、`tempfile.mkdtemp()` のパスを返して後始末しない。後者はモジュール定数 `CERTFILE` / `KEYFILE` として import 時に 1 回だけ実行される
- `_encode_capsule` が `tests/conftest.py` と `tests/test_webtransport_h3_close_session_message.py` に同一実装で存在する

## 設計方針

- C++ 側の共通化先は既存の `src/bindings/header_convert.h` を拡張するか、新たな共通ヘッダを設ける。名前は実態に合わせて一般化する。`is_valid_utf8` のコメントは層ごとの仕様参照が異なるため、共通ヘッダには一般的な説明を置き、層固有の参照は呼び出し側に残す
- 可変長整数は「成功時の値と消費バイト数を返す共通ヘルパー」に寄せ、失敗の表現は呼び出し側に残す。`src/bindings/quic.cpp` の `decode_varint` は `consumed` 出力をやめて共通ヘルパーの戻り値を使う形に書き換える (パケットパースの呼び出し 2 箇所も合わせる)
- WT_APPLICATION_ERROR の写像は片方から他方を呼ぶ形にしない (C++ は順方向のみで Python から呼べず、Python を C++ の送信経路から呼ぶのも現実的でない)。順方向が二重実装であることがリスクであるため、仕様の端点 (`0x52E4A40FA8DB` / `0x52E5AC983162`) と draft の式から期待値を独立に与え、両実装が一致することを PBT で検証する。C++ 側へは公開済みの `H3Session::map_send_error_code` を使う。既存の `tests/test_webtransport_h3_error_code_remap.py` のオラクル比較は維持する
- Python 側の共通化先は `src/webtransport/_common.py` とする。既存の集約方針 (0125 の `_common.py` 新設) に従う。新規ヘルパーの型は `Any` ではなく `Protocol` で書く
- `_timeout_seconds` はクライアント用とサーバー用の 2 つの形にする。クライアント用は `timeout_ns <= 0` で 0.0 を返す挙動を含めて共通化する。サーバー用は期限を返す接続集合を引数で受ける形にし、`_conn_addr` と `_clients` の差を吸収する
- `_send_pending` は UDP 送信の 3 箇所のみを対象とする。`h2` / `http2` は戻り値の型も送信機構も異なるため対象外とする
- 証明書生成は生成部分だけを共通化し、プロファイルとライフサイクルは呼び出し側に残す。`test_certificates` は SAN 付きプロファイルと `TemporaryDirectory` の後始末を維持し、`create_test_certificates` は現行のプロファイルと import 時の 1 回実行という性質を維持する。`CERTFILE` / `KEYFILE` のモジュール定数は残す
- テスト側の共通化先は `tests/conftest.py` とし、closed/0028 / 0081 / 0202 と同じ方針で進める。`_create_connection_pair` は `set_max_client_streams_bidi(100)` を引数で切り替えられるようにする
- 挙動を変えない純粋なリファクタリングとする。イベント列やワイヤ出力に差が出ないことをテストで確認する
- 一度にすべてを扱うと差分が大きくなるため、C++ の共通化・Python の共通化・テストの共通化でコミットを分ける

## 完了条件

- `is_valid_utf8`、可変長整数の成功時処理、WT_APPLICATION_ERROR の一致検証、`_resolve_remote`、`_refresh_dcid_index` / `_drop_dcid_index`、`_timeout_seconds`、UDP 送信の `_send_pending`、`_pump`、`_create_connection_pair`、証明書の生成部分、`_encode_capsule` がそれぞれ 1 箇所になる
- WT_APPLICATION_ERROR は両実装が一致することを検証するテストがあり、既存のオラクル比較が維持されている
- 共通化によって挙動が変わっていないことを、既存のテストと追加したテストで確認できる
- 既存の全テストが通過する

## 解決方法

- `src/bindings/webtransport_h2.cpp` / `webtransport_h3.cpp` の `is_valid_utf8` を共通ヘッダへ移す
- `src/bindings/quic.cpp` と `src/bindings/webtransport_h2.cpp` の可変長整数の成功時処理を共通ヘルパーへ統合し、`src/bindings/quic.cpp` の呼び出し 2 箇所を新しい契約に合わせる
- WT_APPLICATION_ERROR の一致検証を `tests/test_webtransport_h3_error_code_remap.py` または `tests/prop_webtransport_h3.py` に追加する (端点と境界値を含む)
- `src/webtransport/_common.py` に送信処理 (UDP)・タイマー期限 (クライアント用とサーバー用)・名前解決・DCID 索引のヘルパーを追加し、各層から呼ぶ
- `tests/conftest.py` に `http3.Connection` 用のポンプとペア生成 (クレジット設定を引数化)、証明書の生成部分、`_encode_capsule` を集約し、各テストファイルの複製を削除する
