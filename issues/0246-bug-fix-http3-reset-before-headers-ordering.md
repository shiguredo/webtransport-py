# http3 の高レベル層で同一 drain の on_stream_reset が on_headers より先に届く

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-reset-before-headers-ordering
- Polished: 2026-09-20

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
  - `on_stream_end` の通知 (QUIC FIN の単一経路) との相対順序だけは現状を維持する (リセットの通知は `on_stream_end` より前に出す)。現行実装は `finished_streams` への追加を `quic_event.fin` と `stream_id % 4 in (0, 1)` だけで判定し、`shutdown_stream_read` は `finished_streams` を消さないため、同一バッチに `STREAM_DATA(fin=True)` と `RESET_STREAM` が並ぶと `on_stream_end` と `on_stream_reset` の両方が発火する (RFC 9000 Section 3.1 は Data Sent 状態から RESET_STREAM を送れると定め、同 Section 3.2 は RESET_STREAM の受信時に既に全データを受信済みであり得ると述べているため、準拠したピアからも生じ得る)。現行の通知順は リセット → ヘッダー/データ → `on_stream_end` であり、保留後は ヘッダー/データ → リセット → `on_stream_end` になる (リセットと `on_stream_end` の相対順序は変わらない)
  - 保留した通知を出す位置は、同一バッチの HTTP/3 イベント drain を終えた後・`on_stream_end` の通知より前とし、複数のリセットは到着順に出す
  - 同一バッチで接続終了 (`CONNECTION_CLOSED`) を観測した場合も保留分は失わない (通知する)。サーバーでは `Server._drain_quic_events` の `CONNECTION_CLOSED` 分岐が `Server._remove_client` を呼ぶため接続終了の処理が HTTP/3 の drain より先に走るが、`Server._process_http3_events` は接続オブジェクトを引数で受け取り `_clients` の登録を見ない (早期 return は接続オブジェクトが `None` のときだけ) ため、保留分は HTTP/3 の drain の後に通知される。クライアントでは接続終了の処理が `Client._running` / `Client._connected` のフラグ更新だけであり、フラグ更新の後に同一周回で通知される。いずれの場合も、先に到着した `HEADERS` / `DATA` の通知の後に、リセットの通知が 1 回だけ届く (`_remove_client` の後でも `Server._send_to` は接続オブジェクトを受けて送信できる)
- **不採用**: QUIC イベント 1 件ごとに HTTP/3 のイベント drain を挟む案。ワイヤ順序は保たれるが、`DATA` / `HEADERS` のコールバックが `CONNECTION_CLOSED` など他の QUIC イベントの処理と交互になり、既存の観測順序 (バッチ単位) を広く変える
- 順序はアプリから見た契約であるため、次の docstring と `skills/webtransport-py/SKILL.md` の HTTP/3 の節に「同一の受信バッチでは、先に到着したデータのコールバックがリセットより先に呼ばれる」旨を明記する
  - `Client.on_headers` と `Client.on_stream_reset` (`src/webtransport/http3/client.py`)
  - `Server.on_request` と `Server.on_stream_reset` (`src/webtransport/http3/server.py`。サーバー側に `on_headers` という setter は無く、ヘッダーのコールバックは `on_request`)
  - `skills/webtransport-py/SKILL.md` の HTTP/3 の節にある `http3.Client` / `http3.Server` のコールバック一覧と `on_stream_end` の説明 (`tests/test_skill_api_consistency.py` は名前の存在しか検査しないため、追記漏れは人手で確認する)
- 変更対象: `src/webtransport/http3/client.py` / `server.py`、`tests/test_e2e_http3_peer_reset.py` (0240 のピアリセットの足場を再利用し、同一バッチの順序を検証するテストを追加する)、`skills/webtransport-py/SKILL.md`

## 完了条件

- 同じ受信バッチで完備した HEADERS とピア起点のリセットを処理したとき、`on_headers` が `on_stream_reset` より先に呼ばれる
- 実 QUIC ピアから完備した HEADERS フレームと低レベル `reset_stream` を送り、DUT 側のコールバック順を記録して検証する (ピアの用意と同一バッチの作り方は次による)
  - **同一バッチの作り方 (クライアント側 DUT)**: DUT の run ループを止めた状態で、ピアが HEADERS のデータグラムと RESET_STREAM のデータグラムを `_send_pending()` を 2 回に分けて送り切る。その後に DUT の run ループを再開すると、`Client._receive` が最初のデータグラムを待って受けたあとに非ブロッキングで残りを読み切るため、両方が 1 回の受信 (同一バッチ) で処理される
  - **同一バッチの作り方 (サーバー側 DUT)**: `Server` の run ループは 1 データグラムごとに `Server._handle_datagram` を呼び、その中で `Server._drain_quic_events` と `Server._process_http3_events` を実行する。したがってピアのフラッシュを 2 回に分けると 2 バッチになり、順序の逆転は再現しない (RED が成立しない)。テストは run ループを使わず、両データグラムを `ClientConnection.quic_connection.receive(...)` へ直接渡したうえで `Server._drain_quic_events` と `Server._process_http3_events` を 1 回ずつ呼び、同一バッチを確定させる (どちらもテストからの直接入力を想定している)
  - **1 回の `_send_pending()` で 1 パケットにまとめる方法は使えない**: リセット対象ストリームの未送信データは送出されない (`QuicConnection::reset_stream` が `shutdown_stream_write` を FLUSH 付きで呼び、あわせて `stream_buffers_` を消すため)。HEADERS と `reset_stream` をまとめて送出すると DUT には RESET_STREAM しか届かず、`on_headers` が呼ばれない (実測で 3 回とも STREAM_RESET のみ)。この方法では修正前後どちらでもテストが失敗する
  - ここでの「同一バッチ」は、1 回の QUIC イベント drain で処理する QUIC イベント列を指す (クライアントは `_receive` 1 回、サーバーは `_handle_datagram` 1 回に対応する)
  - 完備した HEADERS は、ピア側で `http3.Connection.submit_request` した結果を `get_streams_to_send` から取り出した QPACK 符号化済みのバイト列を `send_stream_data` で送る。同じ `get_streams_to_send` に現れるエンコーダーストリームのデータもピアの単方向ストリームへ送る (送らないと DUT 側の QPACK デコードが失敗し HEADERS が届かない)
- クライアント側 (`http3.Client` を DUT とし、`http3.Server` をピアにする) でも同じ順序を検証する (サーバー側と同じく、`on_stream_reset` が 1 回だけ呼ばれること・対照テスト・RED の確認まで行う)
  - クライアント側では、0240 のクライアント側テストの足場 (ピアが生バイトを送るだけ) をそのままは使えない。完備した応答 HEADERS のバイト列を得るには、ピアの `http3.Server` が持つ `http3.Connection` に制御 / QPACK エンコーダー / デコーダーストリームを bind して SETTINGS を交換したうえで `submit_response` を呼ぶ (bind が無いと `submit_response` は False を返し、バイト列が得られない)
  - 同一バッチの作り方はクライアント側の手順 (DUT の run ループを止め、ピアに 2 回フラッシュさせ、run ループを再開して `Client._receive` にまとめて読ませる) を使う
- 同一バッチにピア起点のリセットと接続終了 (`CONNECTION_CLOSED`) が並ぶ場合でも、リセットの通知が接続終了の処理より前に 1 回だけ届く (サーバー側はピアがリセットを送った直後に接続を閉じる構成で検証する)
- リセットのみを受信した場合は `on_stream_reset` が 1 回だけ呼ばれ、`on_headers` (`http3.Client`) / `on_request` (`http3.Server`) が呼ばれない (対照)
- 既存の 0240 の e2e テスト (`tests/test_e2e_http3_peer_reset.py`) と `tests/test_http3.py` の低レベルテストが引き続き通過する
- 各テストはリセット通知の保留を外すと失敗することを実測で確認する (RED。リセットのみを受信する対照テストは現行実装でも通るため RED の対象外とする)
- 全テストが通過する

## 対象外

- ピアの STOP_SENDING の扱い (0245 で扱う)
- `on_stream_reset` と `on_stream_end` の相対順序の変更 (本 issue では現状維持)
- 低レベル `Http3Connection` のイベント順序 (nghttp3 の受信順であり変更しない)
- アプリが `reset_stream` を呼んだ場合の `ResetStream` イベントの順序 (アプリ起点の要求であり本 issue の対象ではない)
- `STOP_SENDING` 分岐の追加 (0245) は同じ QUIC イベント drain を触るため、実装の順序によっては rebase が必要になる
