# conftest.py の手書きイベント取り出しループを _drain_events に寄せ替える

- Created: 2026-08-14
- Completed: 2026-09-12
- Branch: feature/refactor-conftest-drain-loop-consolidation
- Polished: {YYYY-MM-DD}

## 目的

`tests/conftest.py` に汎用のイベント取り出しヘルパー `_drain_events` (next_event() が None を返すまで取り出す) が追加されたことで、既存ヘルパー内の手書きの取り出しループが実質重複になった。これらを `_drain_events` に寄せ替えて、イベントの取り出し仕様の変更を 1 箇所で済ませられるようにする (closed issue 0073 の集約の流れの継続)。

## 現状

`tests/conftest.py` に手書きの「next_event() が None を返すまで取り出す」ループが 3 ヘルパーに残っている:

- `_accept_session` (サーバー側): SESSION_READY を数えながら取り出し、多重発火を assert で失敗させる
- `_drain_session_ready` (クライアント側): SESSION_READY の最後のセッション ID を返し、多重発火を assert で失敗させる
- `_connect_h2_session`: サーバー側・クライアント側の 2 箇所で SESSION_READY を集める手書きループを持つ

これらは `_drain_events` で全イベントを取り出してから SESSION_READY をフィルタする形に書き換え可能だが、SESSION_READY の集計と多重発火チェック (assert) が混在しているため単純置換はできない。

## 設計方針

- `_drain_events` で全イベントを取り出し、リスト内包表記等で SESSION_READY を集計する形に書き換える
- 各ヘルパーのアサーション強度 (多重発火でテストを失敗させる、受理セッション ID の検査等) は維持する
- テスト本体の挙動は変えない純粋なリファクタリングとする

## 完了条件

- `tests/conftest.py` 内の手書きイベント取り出しループが `_drain_events` を使う形になる
- 全テストが通る

## 解決方法

`tests/conftest.py` の 3 ヘルパーに残っていた手書きのイベント取り出しループを `_drain_events` に寄せ替えた。

- `_accept_session`: `_drain_events(server)` で全イベントを取り出し、`h3.EventType.SESSION_READY` でフィルタしたリストに対して「1 件以下であること (多重発火チェック)」「1 件であること (セッション確立)」を assert し、受理とセッション ID の返却を行う形にした
- `_drain_session_ready`: 同様にフィルタしたリストから SESSION_READY が無ければ -1、あれば最後の 1 件のセッション ID を返す形にした。多重発火チェックは据え置き
- `_connect_h2_session`: サーバー側・クライアント側の 2 箇所のループを `_drain_events` とフィルタに置き換えた。assert の種類と文言は従来どおり
- `_drain_events` と `_EventSource` をファイル末尾から import 直後へ移し、利用箇所より前に定義されるようにした。取り出し仕様を 1 箇所に閉じ込める意図を docstring に追記した

挙動は変更していない純粋なリファクタリングであり、`uv run pytest tests/ --timeout=30` の 1057 件が全て通ることを確認した。
