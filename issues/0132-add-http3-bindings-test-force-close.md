# HTTP/3 bindings にテスト専用の `closed_` 強制セットヘルパを追加する

- Created: 2026-08-21
- Completed: {YYYY-MM-DD}
- Branch: feature/add-http3-bindings-test-force-close
- Polished: {YYYY-MM-DD}

## 目的

`webtransport.webtransport_ext.http3.Connection` にテスト専用のヘルパ (`_test_force_close()` 等) を追加し、Python 側から `Connection.is_closed() == True` かつイベント無しの状態を人工的に作れるようにする。これにより、0107 で追加した `webtransport.http3.Client.run` / `Server.run` の `is_closed()` チェック経路 (HTTP/3 プロトコルエラーで nghttp3 が負値 return したケース) が実際に発火するかを e2e で単独検証できるようにする。

## 現状

- `src/bindings/http3.cpp` の `Http3Connection::receive_stream_data` (`nghttp3_conn_read_stream2` が負値を返す分岐) と `Http3Connection::get_streams_to_send` (`nghttp3_conn_writev_stream` が負値を返す分岐) は 0107 で `closed_ = true` を立てるようになったが、Python 側の任意バイト入力からこれらの負値経路を誘発するのは困難
- nghttp3 は通常のプロトコルエラー (RFC 9114 Section 8) を内部で GOAWAY 化し、`read_stream2` / `writev_stream` は正常終了を返す。負値が返るのは `NGHTTP3_ERR_CALLBACK_FAILURE` / `NGHTTP3_ERR_NOMEM` などにほぼ限定される
- 0107 の実装フェーズで、これらの経路を Python 側から誘発するテストが不能と判明。0107 は「defensive check + QUIC CONNECTION_CLOSE 送出」と回帰テスト 2 本 (`test_http3_client_run_exits_on_client_close` / `test_http3_server_removes_client_on_client_close`) で closed になったが、追加した `is_closed()` チェック単独の効果を検証するテストは持てないままになっている
- `src/webtransport/http3.pyi` (`Connection` インタフェース定義) にもテスト専用ヘルパは無い
- 先例 0129 は HTTP/2 版 (`Http2Connection`) について同種のテスト専用ヘルパ追加を扱う。本 issue はその HTTP/3 版

## 設計方針

- `src/bindings/http3.cpp` の `Http3Connection` クラスに `_test_force_close()` メソッドを追加し、内部で `closed_ = true` を立てるだけの実装にする。イベント push はしない (フレームエラーで低レベルが自主クローズしたケースの再現)
- nanobind の `.def(...)` で Python から呼べるようにする。名前は `_` prefix で「テスト専用・非公開 API」であることを示す
- `src/webtransport/http3.pyi` にも `_test_force_close(self) -> None` を追加する (型チェックのため)
- production の高レベル API (`webtransport.http3.Client` / `Server`) からは呼ばない
- `Http3Connection::_test_force_close` の docstring と bindings 側のヘッダファイル (`src/bindings/http3.h`) にも「テスト専用。production コードから呼ばない」の旨を明記する

## 完了条件

- `src/bindings/http3.cpp` の `Http3Connection` クラスに `_test_force_close()` メソッドが追加され、Python から `Connection._test_force_close()` として呼べる
- 呼び出し後に `Connection.is_closed() == True` になり、`next_event()` は None を返す (イベントは push されない)
- `src/webtransport/http3.pyi` にシグネチャが追加され、`ty check` を通る
- `tests/test_http3.py` 等に低レベル動作確認テストを追加する (`Connection._test_force_close()` 後の `is_closed()` と `next_event()` を確認)
- `tests/test_e2e_http3.py` に `webtransport.http3.Client.run` と `Server.run` のフレームエラー経路独立検証テストを追加する: 実 Server-Client 対で接続後、`_http3_connection._test_force_close()` を呼び、QUIC 側の `CONNECTION_CLOSED` イベントを介さずに 0107 で追加した is_closed() チェック経路のみで `Client.run()` / `Server.run` の該当 client 回収が発火することを検証する
- テスト全 pass、`ruff format` / `ruff check` / `ty check` 通過

## 解決方法

- 変更対象: `src/bindings/http3.cpp`
  - `Http3Connection` クラスに `void _test_force_close()` メソッドを追加。実装は `closed_ = true` のみ
  - `NB_MODULE` の `.def(...)` で `_test_force_close` を Python に公開
- 変更対象: `src/bindings/http3.h`
  - `_test_force_close()` のメソッド宣言と docstring 追加
- 変更対象: `src/webtransport/http3.pyi`
  - `Connection` クラスに `def _test_force_close(self) -> None:` を追加
- 変更対象: `tests/test_http3.py` (または低レベル向けの適切なテストファイル)
  - `_test_force_close()` を呼んだ後に `is_closed()` が True で `next_event()` が None であることを確認する低レベルテストを追加
- 変更対象: `tests/test_e2e_http3.py`
  - 0107 で保留になった「HTTP/3 プロトコルエラー経路の独立検証」テスト (`test_http3_client_run_exits_on_frame_error` / `test_http3_server_removes_client_on_timeout_path_after_close` 相当) を追加
- 変更対象外: production コード (`src/webtransport/http3/client.py` / `server.py`) からは `_test_force_close()` を呼ばない

## 依存関係

- 本 issue は 0107 (closed 予定) と 0129 (open、HTTP/2 版) の完了後に着手するのが自然
- 0129 と本 issue はテストヘルパ設計 (`_test_force_close`) の名前と方針を揃えるべき (対称性の維持)
