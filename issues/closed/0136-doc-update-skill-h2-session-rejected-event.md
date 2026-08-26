# skills/webtransport-py/SKILL.md のイベント型の記述に SESSION_REJECTED / status_code / headers を反映する

- Created: 2026-08-23
- Completed: 2026-08-26
- Branch: feature/update-skill-h2-session-rejected-event
- Polished: {YYYY-MM-DD}

## 目的

`skills/webtransport-py/SKILL.md` の「イベント型 (EventType)」セクションが、bindings で追加された `h2.EventType.SESSION_REJECTED` と `h2.Event.status_code` / `h2.Event.headers` フィールドに追従しておらず、LLM が拒否イベントの受信処理を書けない。記述を実装と一致させる。

## 現状

- `skills/webtransport-py/SKILL.md` の「イベント型 (EventType)」セクションには `h2.EventType` として `SESSION_READY` / `SESSION_CLOSED` / `SESSION_DRAINING` / `STREAM_DATA` / `STREAM_RESET` / `STOP_SENDING` / `DATAGRAM` / `ERROR` の列挙のみで、`SESSION_REJECTED` の記述がない
- 同セクションの `h2.Event` フィールド一覧に `status_code` / `headers` がなく、`h3.Event` の `is_unidirectional` 等との整合が取れていない
- C 拡張 (実装) 側では `H2EventType::SessionRejected` (末尾追加)、`H2Event.status_code` / `H2Event.headers` が公開済みで、生成される `src/webtransport/h2.pyi` にも反映されている
- 過去に SKILL.md と実装のドリフトを解消した issue (0012 / 0043) の流れである

## 設計方針

- `skills/webtransport-py/SKILL.md` の「イベント型 (EventType)」セクションを更新する:
  - `h2.EventType` の列挙に `SESSION_REJECTED` を追加する (非 2xx 応答の拒否通知。`SESSION_CLOSED` とは意味論が異なる旨も併記する)
  - `h2.Event` のフィールド一覧に `status_code` (SessionRejected でのみ意味を持つ) と `headers` (SessionReady でのみ意味を持つ。疑似ヘッダーを含む) を追加する
- 実装を参照し、他のレイヤー (quic / http3 / http2) のドリフト修正は対象外とする

## 完了条件

- `skills/webtransport-py/SKILL.md` の「イベント型 (EventType)」セクションが `h2.EventType.SESSION_REJECTED` / `h2.Event.status_code` / `h2.Event.headers` を記載し、bindings と一致する
- 他のレイヤーのドリフトが増えていない (対象外の明記を維持)

## 解決方法

- `h2.EventType` の列挙に `SESSION_REJECTED` を追加し、`SESSION_CLOSED` との意味論の差を併記した
- `h2.Event` のフィールドに `status_code` / `headers` を追加し、`h2.pyi` の docstring と一致させた
