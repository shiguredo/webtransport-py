# 層間に欠けている公開 API を追加する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/add-missing-layer-apis
- Polished: {YYYY-MM-DD}

## 目的

同じ概念を扱うモジュール間で公開 API の有無が揃っておらず、利用者が層を選ぶと観測も制御もできない操作がある。層間で欠けている公開 API を追加して、同じ用途がどの層でも成立するようにする。

## 現状

ストリーム終了の観測:

- `src/webtransport/http2/client.py` / `http2/server.py` / `http3/client.py` / `http3/server.py` には `on_stream_end` がある
- `src/webtransport/h3/client.py` / `h3/server.py` / `h2/client.py` / `h2/server.py` には `on_stream_end` が無い。低レベルの `h3.EventType.STREAM_CLOSED` は高レベル層のイベント分岐で扱われず破棄され、`h2` の `Event.fin` も `on_stream_data` に渡されない

ストリームの中断:

- `src/webtransport/quic/client.py` の `Client.shutdown_stream` に対応する API が `src/webtransport/quic/server.py` の `Server` に無い。`h3.Server` は `reset_stream` / `close_stream`、`h2.Server` の `SessionWriter` は `reset_stream` / `stop_sending` を持つ
- `src/webtransport/http3/client.py` の `Client.reset_stream` と `http3/server.py` の `Server.reset_stream` に対し、高レベルの `http2.Client` / `http2.Server` に中断 API が無い (低レベルの `http2.Connection.reset_stream` は存在する)

再エクスポート:

- `webtransport_ext/h2.pyi` に `CapsuleType` が公開されているが、`src/webtransport/h2/__init__.py` の import と `__all__` に無い。他の 4 層はスタブの公開名を再輸出している
- `src/webtransport/http3/constants.py` の docstring は HTTP/3 のエラーコード定数を「`Error` イベントの値と比較するために公開する」と書いているが、`src/webtransport/http3/__init__.py` が再輸出するのは `H3_GENERAL_PROTOCOL_ERROR` のみで、他は `webtransport.http3.constants` を明示的に import しないと到達できない

## 設計方針

- ストリーム終了の観測は、低レベル層が既に持っている情報を高レベル層のコールバックとして surface するだけで済む。`h3` は `EventType.STREAM_CLOSED` を、`h2` は `Event.fin` を使う
- ストリームの中断は、各層の既存 API (`shutdown_stream` / `reset_stream` / `close_stream`) を対応する層へ追加する。アドレス駆動の層では既存メソッドと同じく接続を特定する引数を取る
- 再エクスポートは import と `__all__` に追加するだけにする。`http3` の定数は到達経路を二重に持つことになるが、docstring が意図として書いている公開方針に合わせる
- `tests/test_type_stub_layout.py` と `tests/test_skill_api_consistency.py` の検査対象に追加分が含まれることを確認する

## 完了条件

- `h3` / `h2` の `Client` と `Server` に `on_stream_end` があり、FIN 受信で 1 回だけ発火する
- `quic.Server` にストリーム中断 API があり、`h3.Server` と同じ意味で動作する
- 高レベルの `http2.Client` / `http2.Server` に `reset_stream` がある
- `webtransport.h2.CapsuleType` と `webtransport.http3` の各エラーコード定数が import できる
- 追加分のテストが入り、全テストが通過する

## 解決方法

- `src/webtransport/h3/client.py` / `h3/server.py` のイベント分岐に `STREAM_CLOSED` を追加し、`on_stream_end` の setter と発火を実装する。`src/webtransport/h2/client.py` / `h2/server.py` も同様に `Event.fin` を使う
- `src/webtransport/quic/server.py` に `Server.shutdown_stream(addr, stream_id, error_code=0)` を追加する
- `src/webtransport/http2/client.py` / `http2/server.py` に `reset_stream` を追加する
- `src/webtransport/h2/__init__.py` と `src/webtransport/http3/__init__.py` の import と `__all__` を更新する
- `skills/webtransport-py/SKILL.md` の該当節を更新する
