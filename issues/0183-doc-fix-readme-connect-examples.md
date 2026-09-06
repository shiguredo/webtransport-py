# README の h3 / h2 クライアント例の connect() が例外送出型に対して bool 判定のまま常に「接続失敗」で終了する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/doc-fix-readme-connect-examples
- Polished: {YYYY-MM-DD}

## 目的

README の h3 クライアント例と h2 クライアント例は `if not await client.connect(): print("接続失敗"); return` の形。`h3.Client.connect` / `h2.Client.connect` は `-> None` (例外送出型) に変更されているため、`not None` は常に True で例が実行すると必ず「接続失敗」を print して return する (成功しても)。CHANGES.md `[CHANGE] Client.connect を例外送出型に変更` (行 20) に対応する README の更新漏れ。examples はすでに例外型に更新済みで README だけが古い。README は初見の看板のため優先修正。

## 現状

- `README.md:139-141` (h3 クライアント例):
  ```python
  if not await client.connect():
      print("接続失敗")
      return
  ```
- `README.md:233-235` (h2 クライアント例) も同型
- `src/webtransport/h3/client.py` の `Client.connect` は `async def connect(self, timeout: float = 10.0) -> None:`
- `src/webtransport/h2/client.py` の `Client.connect` も `-> None`
- 対照: `README.md:315` (QUIC クライアント例) は `if not await client.connect(): print("接続失敗"); return` で `quic.Client.connect` は `-> bool` のため現在も動く (issue 別途で bool 廃止予定)
- `examples/webtransport/h3_client.py:36-41` と `h2_client.py:36-41` は既に try / except に更新済み
- SKILL.md も `webtransport-py-70` の対応で修正予定 (issue 0185)

## 設計方針

- `README.md` の h3 / h2 クライアント例を `try: await client.connect(); except WebTransportConnectError as exc: print(f"接続失敗: {exc}"); return` の形に修正する
- QUIC 例 (`README.md:315`) は現状 bool のため触らない (bool → 例外への統一は別 issue 予定)
- SKILL.md (`webtransport-py-70` プロジェクトの skills/) と齟齬がないことを確認
- `examples/` の実装と同じ書き方で揃える

## 完了条件

- README の h3 / h2 クライアント例がコピー&ペーストで動作すること
- `WebTransportConnectError` の import 例も含まれること
- 既存のテスト全 822 件が引き続き通過すること
