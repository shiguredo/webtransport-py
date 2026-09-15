# 層間に欠けている公開 API を追加する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/add-missing-layer-apis
- Polished: 2026-09-15

## 目的

同じ概念を扱うモジュール間で公開 API の有無が揃っておらず、利用者が層を選ぶと観測も制御もできない操作がある。本 issue は層間 API の非対称のうち、ストリーム終了の観測・ストリームの中断・再エクスポートの 3 点を対象とし、欠けている公開 API を追加する。`on_stop_sending` / `on_error` / `on_goaway` など他の非対称は対象外とする。

## 現状

ストリーム終了の観測:

- `src/webtransport/http2/client.py` / `http2/server.py` / `http3/client.py` / `http3/server.py` には `on_stream_end` がある
- `src/webtransport/h3/client.py` / `h3/server.py` / `h2/client.py` / `h2/server.py` には `on_stream_end` が無い
- 低レベルの `h3.EventType.STREAM_CLOSED` は高レベル層のコールバックに surface されない。ただし `STREAM_CLOSED` は FIN の通知ではなく、アプリ起点の `close_stream`、ピアの `RESET_STREAM` の処理、セッション終了時の後始末でも発火する (`tests/test_webtransport_h3_ghost_stream.py` が「ピア FIN なしのリセット」で発火することを検証している)。`h3.Event` に fin に相当するフィールドも無い
- `src/webtransport/h3/client.py` と `h3/server.py` の受信ループは `quic.EventType.STREAM_DATA` の `fin` を既に参照している
- `src/webtransport/http3/client.py` は同じ問題を「QUIC FIN の単一経路」で解いている。接続単位の `finished_streams` に `quic_event.fin` かつ `stream_id % 4 in (0, 1)` のストリームを記録し、イベント処理の後に通知する
- `h2` の `Event.fin` は低レベルで設定されるが、高レベル層のイベント分岐は `event.stream_id` と `event.data` しか `on_stream_data` へ渡さず、fin は surface されない

ストリームの中断:

- `src/webtransport/quic/client.py` の `Client.shutdown_stream` に対応する API が `src/webtransport/quic/server.py` の `Server` に無い。`h3.Server` は `reset_stream` / `close_stream`、`h2.Server` の `SessionWriter` は `reset_stream` / `stop_sending` を持つ
- `http2` 層に中断 API が無いのは `Client` と `Server` の両側である。`src/webtransport/http2/client.py` の `Client` には `reset_stream` / `stop_sending` が無く、`src/webtransport/http2/server.py` の `ResponseWriter` も `send_headers` / `send_data` / `drain` のみで、`h2.Server` の `SessionWriter` が持つ `reset_stream` / `stop_sending` に相当する API が無い
- 到達できるのは低レベル Sans-I/O の `webtransport_ext.http2.Connection.reset_stream` のみである。同層は HTTP/2 フレーム層の RST_STREAM を送出する API であり、`h2` 層の `Client.reset_stream` / `SessionWriter.reset_stream` (WebTransport カプセル層で状態検証とエラーコードのリマップを行う) とは層も意味も異なる

再エクスポート:

- `webtransport_ext/h2.pyi` に `CapsuleType` が公開されているが、`src/webtransport/h2/__init__.py` の import と `__all__` に無い
- `src/webtransport/http3/constants.py` の docstring は HTTP/3 のエラーコード定数を「`Error` イベントの値と比較するために公開する」と、公開する理由を書いている。`__all__` は 11 個の定数を持つが、`src/webtransport/http3/__init__.py` が再輸出するのは `H3_GENERAL_PROTOCOL_ERROR` のみで、他は `webtransport.http3.constants` を明示的に import しないと到達できない

## 設計方針

- `on_stream_end` は `http3` と同じ「QUIC FIN の単一経路」で実装する。`h3` の受信ループで `quic.EventType.STREAM_DATA` の `fin` を接続単位に記録し、WebTransport のデータストリーム (`stream_id % 4 in (0, 1)`) に限定して、イベント処理の後に 1 回だけ通知する。`h3.EventType.STREAM_CLOSED` は発火源に使わない
- `h2` は `Event.fin` を `on_stream_end` として surface する。`on_stream_data` に fin を渡す形にはせず、終了は専用のコールバックで通知して 1 回だけ発火させる
- `quic.Server.shutdown_stream` は `quic.Client.shutdown_stream` と同じ意味にする。QUIC フレーム層の API であり、`h3.Server.reset_stream` が行う nghttp3 への通知とエラーコードのリマップは行わない
- `http2.Client` と `http2.ResponseWriter` に `reset_stream` を追加する。低レベル `webtransport_ext.http2.Connection.reset_stream` が HTTP/2 フレーム層の RST_STREAM を送出するため、`h2` 層の同名 API (WebTransport カプセル層で状態検証とエラーコードのリマップを行う) とは層も意味も異なる。この意味差を docstring に書き、利用者が層を選べるようにする
- `stop_sending` は `http2` 層には追加しない。低レベル `webtransport_ext.http2.Connection` に `stop_sending` が無く (`reset_stream` のみ)、`h2` 層の `stop_sending` は WT_STOP_SENDING カプセルを送出する API であるため、HTTP/2 フレーム層である `http2` 層には同等の操作が存在しない。`http2` 層で送信停止が必要な場合は `reset_stream` を使う
- 再エクスポートは import と `__all__` への追加だけで行う。`webtransport.http3.constants` のサブモジュール経路も残し、到達経路を二重にする意図を `src/webtransport/http3/__init__.py` に書く
- `tests/test_type_stub_layout.py` と `tests/test_skill_api_consistency.py` の検査対象に追加分が含まれることを確認する

## 完了条件

- `h3` / `h2` の `Client` と `Server` に `on_stream_end` があり、FIN の受信で 1 回だけ発火する。リセットやセッション終了では発火しない
- `quic.Server.shutdown_stream(addr, stream_id, error_code=0)` が `quic.Client.shutdown_stream` と同じく RESET_STREAM と STOP_SENDING を送出する
- `http2.Client` と `http2.ResponseWriter` に `reset_stream(stream_id, error_code=0)` があり、HTTP/2 フレーム層の RST_STREAM を送出する (`h2` 層の `Client` / `SessionWriter` の同名 API は WebTransport カプセル層で状態検証とエラーコードのリマップを行うため、層が異なることを docstring で区別する)
- `webtransport.h2.CapsuleType` と `webtransport.http3` の各エラーコード定数 (`src/webtransport/http3/constants.py` の `__all__` の 11 個) が import できる
- 追加分のテストが入り、全テストが通過する

## 解決方法

- `src/webtransport/h3/client.py` / `h3/server.py` に `on_stream_end` の setter を追加し、QUIC FIN の単一経路 (`src/webtransport/http3/client.py` の `finished_streams` と同じ形) で発火させる。`stream_id % 4 in (0, 1)` のデータストリームに限定する
- `src/webtransport/h2/client.py` / `h2/server.py` に `on_stream_end` の setter を追加し、`Event.fin` で発火させる
- `src/webtransport/quic/server.py` に `async def shutdown_stream(self, addr: tuple[str, int], stream_id: int, error_code: int = 0) -> None` を追加する (`self._connections` から addr で接続を引き、`close_stream` を呼んで送信を drain する)
- `http2.Client` に `async def reset_stream(self, stream_id: int, error_code: int = 0) -> None` を追加し、`src/webtransport/http2/server.py` の `ResponseWriter` にも同じシグネチャで追加する (低レベル `http2.Connection.reset_stream` へ委譲する)
- `src/webtransport/h2/__init__.py` に `CapsuleType` を、`src/webtransport/http3/__init__.py` に `constants.py` の `__all__` の 11 個を追加する (import と `__all__` の両方)
- `skills/webtransport-py/SKILL.md` の `h3` / `h2` / `quic.Server` / `http2.ResponseWriter` の各節と再エクスポートの注意点を更新する。再エクスポートの追加により、`h2.CapsuleType` は再エクスポートされていない旨の注意点と、`webtransport.http3` が再輸出する定数の記述が実装と一致しなくなるため、あわせて直す (別のドキュメント修正 issue が同じ記述を対象にしているため、実装順によってはそちらの前提が変わる点を申し送る)
