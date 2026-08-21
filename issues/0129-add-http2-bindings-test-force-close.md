# HTTP/2 bindings にテスト専用の `closed_` 強制セットヘルパを追加する

- Created: 2026-08-21
- Completed: {YYYY-MM-DD}
- Branch: feature/add-http2-bindings-test-force-close
- Polished: {YYYY-MM-DD}

## 目的

`webtransport.webtransport_ext.http2.Connection` にテスト専用のヘルパ (`_test_force_close()` 等) を追加し、Python 側から `Connection.is_closed() == True` かつイベント無しの状態を人工的に作れるようにする。これにより、0113 (closed) で追加した `webtransport.http2.Client.run` の `is_closed()` チェックが「フレームエラーで低レベルが自主クローズ」経路で実際に効くことをテストで単独検証できるようにする。

## 現状

- `src/bindings/http2.cpp` の `Http2Connection::receive` (`nghttp2_session_mem_recv` が負値を返す分岐) と `Http2Connection::send` (`nghttp2_session_mem_send` が負値を返す分岐) は `closed_ = true` を立てるが、Python 側の任意バイト入力からこれらの負値経路を誘発するのは困難
- nghttp2 は通常のプロトコルエラー (RFC 9113 Section 5.4 の Connection Error、Section 5.5 の未知フレーム type 等) を内部で GOAWAY 化し、`mem_recv` / `mem_send` は正常終了 (非負) を返す。負値を返すのは `NGHTTP2_ERR_CALLBACK_FAILURE` / `NGHTTP2_ERR_FLOODED` (数千フレーム規模の flood) / `NGHTTP2_ERR_NOMEM` にほぼ限定される
- 0113 の実装フェーズで、これらの経路を Python 側から誘発するテストが不能と判明。0113 は「defensive check の追加」と回帰テスト 2 本 (GO_AWAY 受信 / close 呼び出し) で closed になったが、追加した `is_closed()` チェック単独の効果を検証するテストは持てないままになっている
- `src/webtransport/http2.pyi` (`Connection` インタフェース定義) にもテスト専用ヘルパは無い

## 設計方針

- `src/bindings/http2.cpp` の `Http2Connection` クラスに `_test_force_close()` メソッドを追加し、内部で `closed_ = true` を立てるだけの実装にする。イベント push はしない (フレームエラー経路の再現)
- nanobind の `.def(...)` で Python から呼べるようにする。名前は `_` prefix で「テスト専用・非公開 API」であることを示す
- `src/webtransport/http2.pyi` にも `_test_force_close(self) -> None` を追加する (型チェックのため)
- production の高レベル API (`webtransport.http2.Client` / `Server`) からは呼ばない
- `Http2Connection::_test_force_close` の docstring と bindings 側のヘッダファイル (`src/bindings/http2.h`) にも「テスト専用。production コードから呼ばない」の旨を明記する

## 完了条件

- `src/bindings/http2.cpp` の `Http2Connection` クラスに `_test_force_close()` メソッドが追加され、Python から `Connection._test_force_close()` として呼べる
- 呼び出し後に `Connection.is_closed() == True` になり、`next_event()` は None を返す (イベントは push されない)
- `src/webtransport/http2.pyi` にシグネチャが追加され、`ty check` を通る
- `tests/test_http2.py` 等に低レベル動作確認テストを追加する (`Connection._test_force_close()` 後の `is_closed()` と `next_event()` を確認)
- `tests/test_e2e_http2.py` に `webtransport.http2.Client.run` のフレームエラー経路独立検証テストを追加する: 実 Server-Client 対で接続後、`client._connection._test_force_close()` を呼び、GO_AWAY イベント経路も TCP EOF 経路も close() 経路も使わずに `Client.run()` が終了することを検証する
- テスト全 pass、`ruff format` / `ruff check` / `ty check` 通過

## 解決方法

- 変更対象: `src/bindings/http2.cpp`
  - `Http2Connection` クラスに `void _test_force_close()` メソッドを追加。実装は `closed_ = true` のみ
  - `NB_MODULE` の `.def(...)` で `_test_force_close` を Python に公開
- 変更対象: `src/bindings/http2.h`
  - `_test_force_close()` のメソッド宣言と docstring 追加
- 変更対象: `src/webtransport/http2.pyi`
  - `Connection` クラスに `def _test_force_close(self) -> None:` を追加
- 変更対象: `tests/test_http2.py` (または低レベル向けの適切なテストファイル)
  - `_test_force_close()` を呼んだ後に `is_closed()` が True で `next_event()` が None であることを確認する低レベルテストを追加
- 変更対象: `tests/test_e2e_http2.py`
  - 0113 で保留になった「フレームエラー経路の独立検証」テストを追加。既存 2 テスト (`test_client_run_exits_on_close` / `test_client_run_exits_on_goaway_injection`) と並べる
- 変更対象外: production コード (`src/webtransport/http2/client.py` / `server.py`) からは `_test_force_close()` を呼ばない
