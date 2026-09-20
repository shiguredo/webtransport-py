# http3 の高レベル層で同一 drain の on_stream_reset が on_headers より先に届く

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-reset-before-headers-ordering
- Polished: {YYYY-MM-DD}

## 目的

`src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` の run ループは、QUIC 層のイベントをすべて処理してから HTTP/3 層のイベントを処理する。QUIC の `STREAM_RESET` は先のループで `on_stream_reset` を呼ぶため、同じ受信バッチに「完備した HEADERS を含む `STREAM_DATA`」と「`STREAM_RESET`」が並ぶと、アプリはヘッダーより先にリセット通知を受け取る。ワイヤ上の到着順 (HEADERS が先) とアプリが観測する順序が逆転し、「ヘッダーで作った状態をリセットで破棄する」実装は、リセットの後にヘッダーのコールバックが来ることで壊れる。

## 現状

- クライアント: `Client.run` は `while True:` で `self._quic_connection.next_event()` を回すループの中で `quic_low.EventType.STREAM_RESET` を処理し、その中で `Http3Connection.shutdown_stream_read` を呼んだうえで `await self._on_stream_reset(...)` を実行する。QUIC のループを抜けた後の別のループで `self._http3_connection.next_event()` を回し、`http3_low.EventType.HEADERS` を `await self._on_headers(...)` へ渡す
- サーバー: 同じく `client.quic_connection.next_event()` のループで `STREAM_RESET` を処理して `on_stream_reset` を呼び、その後に `client.http3_connection.next_event()` のループで `HEADERS` を `on_headers` へ渡す
- 同一バッチで両方が処理される状況は、ピアが完備した HEADERS を含む `STREAM_DATA` と `RESET_STREAM` を続けて送り、DUT の `_receive` が両方を 1 回の受信で読み取った場合に生じる。`on_stream_reset` は先のループ、`on_headers` は後のループで呼ばれるため、アプリはリセットを先に観測する
- `Http3Connection::shutdown_stream_read` は `stream_buffers_` と `pending_headers_` (受信途中のヘッダーブロック) を解放するだけで、`events_` に既に積まれたイベントは消さない。`STREAM_DATA` を `receive_stream_data` へ渡した時点で完備した HEADERS イベントは積まれているため、リセットを先に通知した後で HEADERS が届く
- この順序は 0240 で導入したものではなく、0240 以前から同じ構造である。0240 の完了条件に含めた「ピアがリセットしたストリームで `on_headers` が呼ばれたか」の検証対象にもなっていない
- 期待する順序は「先に到着したデータのイベント → リセット」である。QUIC の順序付き配信 (RFC 9000 Section 2) により、同一バッチ内では `next_event` が返す順序がワイヤの到着順になる
- 低レベルで実測した結果: `http3.Connection` のペアで完備した HEADERS を `receive_stream_data` へ渡した直後に `shutdown_stream_read` を呼び、その後に `next_event` を取り出すと、HEADERS イベントは解放されずに取り出せる。すなわち `STREAM_RESET` の処理が先に走っても、先に到着した HEADERS は後の HTTP/3 drain で届く。高レベル層の 2 段の drain と合わせると、アプリは `on_stream_reset` → `on_headers` の順で観測する (期待は `on_headers` → `on_stream_reset`)

## 設計方針

- **採用案**: QUIC フェーズでピア起点のリセットを検出したら `Http3Connection.shutdown_stream_read` はその場で呼び、アプリへの `on_stream_reset` の通知だけを保留する。同一バッチの HTTP/3 イベント drain を終えた後に保留分を到着順で通知する
  - 保留しても後続データが先に届くことはない。`shutdown_stream_read` により当該ストリームの以後の受信データは破棄されるため、リセットより後に届いた `STREAM_DATA` からイベントが生じない。先に届いた HEADERS のイベントだけが HTTP/3 の drain に残る
  - 複数のリセットが同一バッチにある場合は到着順 (`next_event` が返した順) を保つ
  - `on_stream_end` の通知 (QUIC FIN の単一経路) との相対順序は現状を維持する。リセットされたストリームは FIN の経路に乗らないため、保留の追加で入れ替わる通知は無い
- **不採用**: QUIC イベント 1 件ごとに HTTP/3 のイベント drain を挟む案。ワイヤ順序は保たれるが、`DATA` / `HEADERS` のコールバックが `CONNECTION_CLOSED` など他の QUIC イベントの処理と交互になり、既存の観測順序 (バッチ単位) を広く変える
- 順序はアプリから見た契約であるため、docstring (`on_stream_reset` / `on_headers` の説明) と `skills/webtransport-py/SKILL.md` の該当節に「同一の受信バッチでは、先に到着したデータのコールバックがリセットより先に呼ばれる」旨を明記する
- 変更対象: `src/webtransport/http3/client.py` / `server.py`、`tests/`、`skills/webtransport-py/SKILL.md`

## 完了条件

- 同じ受信バッチで完備した HEADERS とピア起点のリセットを処理したとき、`on_headers` が `on_stream_reset` より先に呼ばれる
- 実 QUIC ピア (`quic.Client`) から完備した HEADERS フレームを送った直後に低レベル `_connection.reset_stream(stream_id, error_code)` と `_send_pending()` でリセットを送り、`http3.Server` 側のコールバック順を記録して検証する (完備した HEADERS は、ピア側で `http3.Connection.submit_request` した結果を `get_streams_to_send` から取り出すなど、QPACK 符号化済みのバイト列として用意する)
- クライアント側 (`http3.Client` を DUT とし、`http3.Server` をピアにする) でも同じ順序を検証する
- リセットのみを受信した場合は `on_stream_reset` が 1 回だけ呼ばれ、`on_headers` が呼ばれない (対照)
- 既存の 0240 の e2e テスト (`tests/test_e2e_http3_peer_reset.py`) と `tests/test_http3.py` の低レベルテストが引き続き通過する
- 各テストはリセット通知の保留を外すと失敗することを実測で確認する (RED)
- 全テストが通過する

## 対象外

- ピアの STOP_SENDING の扱い (0245 で扱う)
- `on_stream_reset` と `on_stream_end` の相対順序の変更 (本 issue では現状維持)
- 低レベル `Http3Connection` のイベント順序 (nghttp3 の受信順であり変更しない)
- アプリが `reset_stream` を呼んだ場合の `ResetStream` イベントの順序 (アプリ起点の要求であり本 issue の対象ではない)
- `STOP_SENDING` 分岐の追加 (0245) は同じ QUIC イベント drain を触るため、実装の順序によっては rebase が必要になる
