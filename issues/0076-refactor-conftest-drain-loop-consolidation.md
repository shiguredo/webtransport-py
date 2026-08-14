# conftest.py の手書きイベント取り出しループを _drain_events に寄せ替える

- Created: 2026-08-14
- Completed: {YYYY-MM-DD}
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
