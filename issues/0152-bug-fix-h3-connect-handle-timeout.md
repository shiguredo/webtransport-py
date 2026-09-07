# h3.Client.connect() と http3.Client.connect() の待ちループが QUIC タイマー処理を呼ばず、ハンドシェイクパケット 1 つのロスで永久失敗する

- Created: 2026-09-06
- Completed: 2026-09-08
- Branch: feature/fix-h3-connect-handle-timeout
- Polished: 2026-09-07

## 目的

`h3.Client.connect` は「HANDSHAKE 完了待ち」「SETTINGS 受信待ち」「2xx 応答待ち」の 3 つのループを回すが、いずれも `QuicConnection.get_timeout()` / `handle_timeout()` を呼ばない (`run()` では呼ぶ)。ngtcp2 の loss detection timer が起動されないため、クライアント → サーバー方向のハンドシェイクパケットが 1 つでも失われると再送が発生せず、`connect(timeout=...)` が必ずタイムアウト (deadline 到達) に落ちる。`http3.Client.connect` に至っては timeout 引数も deadline も無く無限待ち。実ネットワークで最初のフライトのロスは頻繁に起きるため、接続確立の信頼性が実用に耐えない。

## 現状

- `src/webtransport/h3/client.py` の `Client.connect` の 3 つの待ちループ (ハンドシェイク・SETTINGS・2xx) はいずれも `get_timeout()` / `handle_timeout()` を呼ばない
- 同じ `Client` の `run()` は `handle_timeout()` を呼ぶ経路がある
- `src/webtransport/http3/client.py` の `Client.connect` は `async def connect(self) -> bool` で timeout 引数無し、`while self._running:` のみで `get_timeout()` / `handle_timeout()` も呼ばない。`CONNECTION_CLOSED` 受信時は `False` 復帰するが、応答の無い宛先ではそのイベントも届かず実質無限待ちになる
- 実験 (UDP リレーでクライアント → サーバー方向の最初の 1 パケットを落とす。検証済み): `h3.Client.connect(timeout=6.0)` が 6.07 秒後に `ConnectTimeoutError`。落とした 1 発以外の中継は 1 発のみで PTO 再送が起きていない
- 対照の報告 (サーバー → クライアント方向を 1 パケット落とすと 0.24 秒で回復する) は手順の記録がなく未検証のため、実装時に再測定する
- `http3.Client.connect` は閉塞ポート宛では応答が無くハングする (上記の無限待ち構造に従う)
- issues/0054 は quic 層の LossyRelay テスト基盤であり未完了である。本 issue の回帰テストは 0054 の完成を待たず、本 issue 側で UDP リレー器具を用意する (器具の重複は 0054 側で整理する)

## 設計方針

- `h3.Client.connect` の 3 つの待ちループそれぞれで、`_send_pending()` 呼び出し直後 (sleep 前) に `run()` 内のタイマー駆動箇所と同形の `get_timeout()` / `handle_timeout()` 呼び出しを追加する
- `http3.Client.connect` にも同形のタイマー駆動呼び出しを追加する。加えて `timeout: float = 10.0` 引数と deadline 制御 (`loop.time()` + timeout による h2 前例準拠の形式) を追加し、期限到達時は `ConnectTimeoutError` を送出する例外送出型 (h3 / h2 対称) に変える。戻り値 `bool` 契約の変更を含むが、timeout 引数の意味のために不可分であり本 issue 内で完結させる
- 回帰テストは新規 `tests/test_connect_loss_recovery.py` に h3 / http3 分を追加し、UDP リレー器具 (最初の中継 1 発を落とす) を同梱する

## 完了条件

- `h3.Client.connect(timeout=6.0)` がクライアント送信 1 パケットのロス下で PTO 再送により deadline 到達前に接続を完了すること (再送発生はリレーカウンタ増加で確認する)
- `http3.Client.connect(timeout=6.0)` が同条件で回復し、無応答宛先では期限超過後の初回反復で `ConnectTimeoutError` を送出すること
- `tests/test_connect_loss_recovery.py` に上記 2 件のテストを追加すること
- 既存のテスト全 834 件が引き続き通過すること

## 解決方法

- `h3.Client.connect` の 3 つの待ちループに損失検出タイマーの駆動を追加する (run() と同形、送出直後・休止前)
- `http3.Client.connect` にタイマー駆動を追加し、`timeout` 引数付き例外送出型 (h3 / h2 対称) に変える。生成失敗と確立中の素の失敗は接続拒否に寄せ、ハンドシェイク前の終了は握手失敗に寄せる。h3 側の生成失敗も対称に寄せる
- 呼び出し側 (テストと examples) を例外送出型に追随させる
- `tests/test_connect_loss_recovery.py` に UDP リレー器具と 4 件のテスト (h3 / http3 の回復、無応答の期限切れ、名前解決失敗の拒否) を追加する
- 全 887 件のテストが通過することと、レビュー 3 周で致命的と重要が 0 件であることを確認した
