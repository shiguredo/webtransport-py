# h3 の on_stream_reset の error_code 契約 (int | None) を SKILL.md と tests で揃える

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-align-on-stream-reset-int-or-none-contract
- Polished: {YYYY-MM-DD}

## 目的

`h3.Client.on_stream_reset` と `h3.Server.on_stream_reset` の `error_code` は実装上 `int | None` (`deliver_stream_reset_error_code` がレンジ外で `None` を返す) だが、SKILL.md は `int`、tests は `int` 型付きコールバックを渡す。契約と実装の食い違いにより、tests をスタブパッケージ化した型検査で有効化すると型エラーになる (実測 2 件)。契約は draft-ietf-webtrans-http3-16 Section 4.4「delivered to the application as a stream reset with no application error code」を反映しており、`int | None` が正。SKILL / tests / CHANGES を実装に揃える。

## 現状

- `src/webtransport/h3/client.py` の `on_stream_reset` シグネチャ: `Callable[[int, int | None], Awaitable[None]] | None`
- `src/webtransport/h3/server.py` の `on_stream_reset` シグネチャ: `Callable[[int | None, int, int | None, tuple[str, int]], Awaitable[None]] | None`
- SKILL.md の記述: `on_stream_reset(session_id: int, stream_id: int, error_code: int, addr)` / `on_stream_reset(stream_id: int, error_code: int)` (いずれも `int` 型)
- `tests/test_e2e_webtransport_h3.py` の 2 箇所で `error_code: int` の型付きコールバックを `server.on_stream_reset` に渡している
- `_error_codes.py` の `deliver_stream_reset_error_code` はレンジ外で `None` を返す実装
- CHANGES.md に `on_stream_reset` の型変更エントリが無い

## 設計方針

- SKILL.md の h3 セクションの `on_stream_reset` シグネチャを `error_code: int | None` に更新する (Client / Server 両方)
- tests のコールバック 2 箇所の型ヒントを `int | None` に修正する
- CHANGES.md の `## develop` に `[CHANGE] WebTransport over HTTP/3 の on_stream_reset の error_code を int | None に変更する` を追記する (draft-ietf-webtrans-http3-16 Section 4.4 の out-of-range 配信仕様のため)
- issue 0181 (32 bit 復元) の実装後も型は `int | None` のまま (out-of-range で None が残る)

## 完了条件

- SKILL.md の h3 セクションの型が実装と一致すること
- tests のコールバック型注釈が実装と一致すること
- CHANGES.md に対応する [CHANGE] エントリが追加されていること
- 既存のテスト全 822 件が引き続き通過すること
