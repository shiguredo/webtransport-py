# 高レベル QUIC クライアントに connect のタイムアウトと max_datagram_frame_size を追加する

- Created: 2026-08-07
- Completed: 2026-08-08
- Branch: feature/add-connect-settings
- Polished: 2026-08-07
- Reporter: @voluntas

## 目的

高レベル `Client` に ngtcp2-py 互換の接続設定 API (`connect` のタイムアウトと `max_datagram_frame_size`) を追加する。

## 現状

- webtransport-py の `Client.connect()` は while ループでハンドシェイク完了を無制限に待ち、ハンドシェイクが進まない場合に無限にブロックする
- ngtcp2-py は `connect(timeout=10.0)` でハンドシェイクを打ち切る
- webtransport-py の `Client` コンストラクタに `max_datagram_frame_size` が無い。低レベル `Config` には `max_datagram_frame_size` / `enable_datagram` があり、既定 (enable_datagram=true / 65536) で DATAGRAM を広告しているが、`Client.connect()` はこれらを設定していない (低レベル既定値がそのまま使われる)
- sora-quic の `test_ngtcp2_datagram.py` が `max_datagram_frame_size=1200` を指定して DATAGRAM を送受信している

## 設計方針

- 変更対象は `src/webtransport/quic/client.py` の高レベル `Client` (コンストラクタと `connect()`)
- `connect(timeout: float = 10.0) -> bool`: ハンドシェイク完了までをタイムアウトで打ち切る。既定値 10.0 は ngtcp2-py と同一で、既存の引数なし `connect()` 呼び出しにも適用される。`timeout <= 0` のときは即座に `False` を返す (ngtcp2-py と同じ)。期限までに確立できない場合は `False` を返す。タイムアウト時は `_running` を落とさず、接続は存続し得る (ハンドシェイクが後で完了する可能性がある)。`_socket` / `_connection` の後始末は `close()` 側に委ねる
- ハンドシェイク待ちの実装方式は、0042 (バックグラウンド受信タスク) が `connect()` の受信ループを再構成するため、0042 の構造に合わせて接続待ちを統合する (0042 を先に実装し、その上でタイムアウトを実装する)
- コンストラクタに `max_datagram_frame_size: int | None = None` を追加し、`connect()` で低レベル `Config` へ反映する:
  - None (既定): 現行のまま (低レベル Config の既定 enable_datagram=true / max_datagram_frame_size=65536 をそのまま使い、既存挙動を維持する)
  - 0: `Config.enable_datagram = False` (DATAGRAM を広告しない)
  - 正の値: `Config.enable_datagram = True` / `Config.max_datagram_frame_size = 指定した値`
  - 負の値: `ValueError` を raise する
- RFC 9221 Section 3 の通り、max_datagram_frame_size は受信サポートの広告であり、DATAGRAM の送信はピアの非ゼロ広告に依存する

## 完了条件

- `connect(timeout=...)` がハンドシェイク未完了のまま期限に達した場合に `False` を返して終了する
- `max_datagram_frame_size` に正の値を指定すると DATAGRAM を受信できる (0 を指定すると広告しない。既定では現行と同じく広告する)
- テストを追加する (connect のタイムアウト / DATAGRAM の広告有無)。DATAGRAM の広告有無は、サーバー側の `remote_max_datagram_frame_size` (0014 の API) でクライアントの広告を観測して確認する
- `connect()` の docstring に `False` の意味 (接続失敗 / タイムアウト) を追記する
- 既存の全テストが通る (既定挙動は現行のままのため、既存の DATAGRAM テストは変更不要)

## 解決方法

- `src/webtransport/quic/client.py` の `connect(timeout: float = 10.0) -> bool` にタイムアウトを追加した。`asyncio.wait_for(self._connect_waiter, timeout=timeout)` でハンドシェイク完了に打ち切りを設け、期限までに確立できない場合は接続を維持したまま `False` を返す (後始末は `close()` が担う)。`timeout <= 0` は接続を開始せず即座に `False` を返す (ngtcp2-py と同じ)。`except TimeoutError` 内で `_task_error` を確認し、バックグラウンドタスクの異常終了 (元の例外が TimeoutError の場合) をタイムアウトと区別して元の例外を伝播する
- コンストラクタに `max_datagram_frame_size: int | None = None` を追加し、`connect()` で低レベル `Config` へ反映する。None (既定) は低レベル既定 (enable_datagram=true / 65536) を維持し、0 は `enable_datagram = False` (広告しない、ローカルの `send_datagram()` も無効化)、正の値は `enable_datagram = True` + 指定値で広告する。範囲外の値 (負または 2^62 - 1 超) は `ValueError` を raise する (RFC 9221 Section 3 の受信サポート広告の意味づけ、RFC 9000 Section 16 の変長整数上限)
- テストは `tests/test_e2e_quic_connect_settings.py` に 9 件を追加した (connect のタイムアウト / timeout<=0 / 正常時の True / タイムアウト後の接続存続と後追いハンドシェイク / DATAGRAM 広告の正値・0・既定 / 上限境界値の受理 / 範囲外の ValueError)
- `skills/webtransport-py/SKILL.md` の `quic.Client` 節に `connect(timeout=...)` / `max_datagram_frame_size` の説明を追加した
- `CHANGES.md` の `## develop` セクションに `[ADD]` エントリを追加した
