# QUIC クライアントのストリーム受信状態の破棄手段を追加する

- Created: 2026-08-08
- Completed: 2026-09-05
- Branch: feature/refactor-recv-state-cleanup
- Polished: 2026-08-26
- Reporter: @voluntas

## 目的

`recv_stream_data` (0037) と `wait_for_stream_reset` (0038) で導入したストリーム受信状態 (`_StreamRecvState`) が接続寿命まで無制限に成長する問題を解消する。受信状態の破棄手段を追加し、メモリ使用量がストリーム数・受信データ量に比例して無制限に増加しないようにする。

## 現状

- `src/webtransport/quic/client.py` の `_StreamRecvState` は次の 4 属性を保持する。0037 由来の受信データ管理 (`data`, `fin`, `event`) と、0038 で追加された STREAM_RESET のエラーコード管理 (`reset_error_code`) が同一オブジェクトに同居する二重責務の状態である
  - `data: bytearray` — 受信データの累積連結
  - `fin: bool` — FIN 受信フラグ
  - `reset_error_code: int | None` — STREAM_RESET 受信時のアプリケーションエラーコード (未受信なら None)
  - `event: asyncio.Event` — 状態更新を待機者へ通知するイベント
- `_recv_states: dict[int, _StreamRecvState]` へのエントリ作成は次の 4 箇所すべてで `setdefault` により行われ、`_recv_states` からエントリを削除する経路は存在しない
  - 受信側: `_update_recv_state` (STREAM_DATA 受信のたび)
  - 受信側: `_handle_stream_reset` (STREAM_RESET 受信のたび。データを一度も受信していないストリームでも RESET 受信ごとにエントリが新規作成される)
  - 待機側: `recv_stream_data` (呼び出し時)
  - 待機側: `wait_for_stream_reset` (呼び出し時)
- 結果として、FIN 完了済みのストリームや RESET 受信済みのストリームも含め全ストリームの受信状態が接続寿命まで保持され続ける
- `recv_stream_data` を一度も呼ばないコールバック専用の利用 (`on_stream_data`) でも、`_handle_received_events` が STREAM_DATA イベントで無条件に `_update_recv_state` を呼ぶため、全ストリームの全受信データが `_recv_states` に累積される。長期間の接続で大量データを扱うとメモリが無制限に増加する
- 「FIN 完了済みストリームの即時 return」要件 (0037) と「RESET 受信済みストリームの即時 return」要件 (0038) のため受信状態の保持自体は必要だが、破棄手段が無い点が問題

## 設計方針

- 変更対象は `src/webtransport/quic/client.py` の高レベル `Client`
- 「自動破棄」と「明示破棄 API」を併用する。保持数の上限による evict は導入しない (Premature Optimization を避ける)
  - **自動破棄**: `recv_stream_data` が FIN を検出して `(bytes, fin)` を正常 return した直後に、当該 `stream_id` のエントリを `_recv_states` から削除する。`recv_stream_data` は呼び出し側が FIN を消費した時点で以後のデータ到着を期待しない使い方であり、正常 return 直後の破棄は自然
  - **明示破棄 API**: `discard_recv_state(stream_id: int) -> None` を高レベル `Client` に追加する。当該 `stream_id` のエントリがあれば削除する。存在しなければ何もしない。コールバック専用の利用者 (`on_stream_data` で消費し `recv_stream_data` を呼ばない) が自分の都合で解放できるようにする
- 破棄対象は `_StreamRecvState` オブジェクトのエントリ全体 (`data` / `fin` / `reset_error_code` / `event` すべて) を `del self._recv_states[stream_id]` で除去する。属性単位の部分破棄はしない
- 破棄後の再受信・再呼び出しの契約:
  - 破棄後にピアから STREAM_DATA / STREAM_RESET が到着した場合、`_update_recv_state` / `_handle_stream_reset` が `setdefault` で新しい空 `_StreamRecvState` を作成する (現行の作成経路と同じ)。以後は通常の状態として扱う
  - 破棄後に `recv_stream_data(stream_id)` を再呼び出しした場合、`setdefault` で新しい空 `_StreamRecvState` が作られ通常の待機ループに入る。ピアが既に FIN を送信済みでこれ以上のデータ到着が無いストリームでは、進捗が無いまま `overall_timeout` で `TimeoutError` になる。破棄済みストリームへの再呼び出しは呼び出し側の責任として避けること (この契約を docstring に明記する)
  - 破棄後に `wait_for_stream_reset(stream_id)` を再呼び出しした場合も同様に、新しい空状態から待機に入り、RESET が届かなければ `timeout` で `TimeoutError` になる
- 0038 (`wait_for_stream_reset`) との整合:
  - 自動破棄は `recv_stream_data` の FIN 正常 return をトリガとする。この時点で `state.reset_error_code` は None のことが通常だが、FIN と RESET が同一 state に届いている場合もあり得る (protocol violation でなくても、実装のイベント発火順で発生し得る)。自動破棄によって `wait_for_stream_reset` の待機者が閉じ込められないよう、破棄前に `state.event.set()` で既存待機者を起床させ、そのあとで `_recv_states` から削除する
  - `wait_for_stream_reset` 側は起床時に `state.reset_error_code` を確認し、None のまま起きたら通常のループに戻り、次周の `setdefault` で新しい空 state を作って待機を継続する (仕様として「同一ストリームへの `recv_stream_data` と `wait_for_stream_reset` の並行呼び出しは、正常 return 時点で `wait_for_stream_reset` 側が新規待機に切り替わる」ことを docstring に明記する)
- 明示破棄 API も同じ順序 (`state.event.set()` → `del self._recv_states[stream_id]`) で破棄する
- 接続終了時 (`_wake_stream_waiters` 経由) の後始末は変更しない。`_recv_states` の全エントリの `event.set()` を呼ぶ既存挙動で、待機者は接続終了として起床する
- 破棄済みストリームを追跡する別集合 (破棄後の再呼び出しで即例外を返すための集合) は導入しない。集合自体が接続寿命まで成長する新たなメモリリークになるため

## 完了条件

- `recv_stream_data` が FIN で正常 return したストリームの `_recv_states` エントリが自動破棄され、`len(client._recv_states)` がストリーム完了とともに減少する
- `Client.discard_recv_state(stream_id)` が追加され、コールバック専用の利用者が任意タイミングで `_recv_states` から解放できる
- 上記いずれの破棄でも、破棄前に `state.event.set()` が呼ばれ、`wait_for_stream_reset` 待機者を含む全待機者が閉じ込められない
- 破棄後の `recv_stream_data` / `wait_for_stream_reset` の再呼び出しは通常の待機ループに入り、進捗が無ければ `overall_timeout` / `timeout` で `TimeoutError` になる。この挙動が両メソッドの docstring に明記される
- テストを追加する
  - 自動破棄: FIN 完了で `recv_stream_data` が正常 return した後に `_recv_states` から当該エントリが消えている
  - 明示破棄: `discard_recv_state(stream_id)` の呼び出しで `_recv_states` から当該エントリが消える。存在しない `stream_id` を渡しても例外にならない
  - コールバック専用: `on_stream_data` のみを登録し `recv_stream_data` を呼ばない使い方で、`discard_recv_state` を明示的に呼ぶことで `_recv_states` が空に戻る
  - 0038 併用: FIN と STREAM_RESET が同一ストリームに届いた場合に、自動破棄が `wait_for_stream_reset` 待機者を起床させ、待機者が新規待機に切り替わる
  - 破棄後の再呼び出し: 破棄した `stream_id` に対する `recv_stream_data` / `wait_for_stream_reset` が `TimeoutError` を送出する
- 既存の全テストが通る

## 解決方法

- `src/webtransport/quic/client.py` に `_discard_recv_state` (待機者起床付きのエントリ除去) と公開 API `discard_recv_state` を追加した
- `recv_stream_data` の FIN 正常 return 3 箇所 (即時・ループ内・タイムアウト同時到達) の直後に自動破棄する
- `recv_stream_data` と `wait_for_stream_reset` の待機ループ先頭でエントリの同一性を確認し、破棄検知時は `setdefault` で新しい空状態から待機を継続する (閉じ込め防止)
- 両メソッドの docstring に破棄後の再呼び出し契約と並行呼び出し仕様を明記した
- `tests/test_e2e_quic_recv_stream_data.py` に 5 本 (自動破棄・明示破棄・コールバック専用・待機者切り替え・破棄後再呼び出し) を追加した。FIN 後のサーバー自動 RESET は RFC 9000 Section 3.5 の MAY のため決定的に駆動できず、待機者切り替えテストでは RESET 注入を行わず起床と切り替えの観測に限定した。残課題として、RESET 保持エントリの FIN 自動破棄パス (破棄前のコード取得は既存の待機者経路で担保) の直接検証は残る
- `CHANGES.md` の本体セクションに `[ADD]` を追加した
