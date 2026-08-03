# skills/webtransport-py/SKILL.md の WebTransport over HTTP/3 の記述を最新化する

- Created: 2026-08-02
- Completed: YYYY-MM-DD
- Branch: feature/update-webtransport-skill
- Polished: 2026-08-03

## 目的

`skills/webtransport-py/SKILL.md` の WebTransport over HTTP/3 の記述を現在の実装と一致させる。スキルドキュメントが実装と食い違うと、LLM がライブラリを使う際に存在しない API を参照したり、実在する API を使い損ねたりする。

## 現状

`skills/webtransport-py/SKILL.md` の h3 セクション (asyncio API の h3 部分・Sans I/O の `h3.Session`・`h3.Config`・`h3.EventType`・「注意点」の h3 関連記述) が最新 API を反映していない:

- `h3.Server.__init__` のシグネチャに `allowed_origins` が無い (`src/webtransport/h3/server.py` の `Server` には実装済み)
- `h3.Server` の主なメソッド一覧に `open_stream` と `close_stream` が無い (同じく実装済み)
- `h3.Client.__init__` のシグネチャに `origin` が無い (`src/webtransport/h3/client.py` の `Client` には実装済み)
- `h3.Client` の主なメソッド一覧に `close_stream` が無い (同じく実装済み)
- Sans I/O の `h3.Session.connect` のシグネチャに `origin` 引数が無い (`src/bindings/webtransport_h3.cpp` の `H3Session::connect` には実装済み)
- Sans I/O の `h3.Session` の一覧に `close_stream` / `reset_stream` / `get_session_ids` / `get_session_streams` / `is_closed` / `set_max_client_streams_bidi` が無い (C 拡張に実装済み)
- `h3.Config` の一覧に `allowed_origins` が無い (C 拡張に実装済み)
- `h3.Event` のフィールド一覧に `is_unidirectional` が無い (C 拡張にフィールドは存在するが、値が設定される経路が無く常に False である)
- `h3.StreamInfo` が SKILL.md 全体で言及されていない (C 拡張に実装済み)
- 「注意点」の「`h3` / `h2` は `unidirectional: bool = False` (どちらもデフォルトは双方向)」が `h3.Server.open_stream` (デフォルト単方向) と矛盾する

## 設計方針

- `skills/webtransport-py/SKILL.md` の h3 セクションのシグネチャとメソッド一覧を、`src/webtransport/h3/server.py`・`src/webtransport/h3/client.py`・`src/bindings/webtransport_h3.cpp` / `.h` の現在の実装に合わせて更新する。検証の参照元は C 拡張のバインディングと asyncio 実装とし、`src/webtransport/h3.pyi` は既存のドリフトがあるため参照元にしない
- サンプルコードもシグネチャの変更に合わせて更新する
- `h3.Server.open_stream` はデフォルト単方向 (`unidirectional=True`)・双方向指定は `NotImplementedError`・失敗時 -1 を返す点を、シグネチャと注記で正確に記載する (draft-ietf-webtrans-http3-16 Section 4.3 の "can" に基づく任意実装)。「注意点」の「`h3` / `h2` は `unidirectional: bool = False` (どちらもデフォルトは双方向)」は、`h3.Client` / `h2` は双方向デフォルト・`h3.Server` のみ単方向デフォルトと書き分ける (asyncio API の話であり、デフォルト値を持たない Sans I/O の `h3.Session.open_stream` とは分けて記載する)
- `h3.Event.is_unidirectional` は値が設定される経路が無く常に False であるため、SKILL.md に記載する場合はその旨を注記する。値の設定は本 issue の対象外とする (必要になったら別 issue とする)。`h3.Client.open_stream` の失敗時挙動 (h3 層の登録失敗を無視して stream_id を返す) も実装上の非対称であり、本 issue の対象外とする
- 他レイヤー (h2 / quic / http3 / http2) の記述は対象外とする。他レイヤーのドリフト (例: quic.Client の 0-RTT 関連 API の欠落) は別 issue として切り出す
- 本 issue は 0009 / 0010 の完了後に実施する (`close_stream` の戻り値が 0009 で、`close_stream` / `reset_stream` の挙動が 0010 で変更されるため、先に書くと 0009 / 0010 のマージで再び古くなる)

## 完了条件

- `skills/webtransport-py/SKILL.md` の h3 セクションが 0009 / 0010 のマージ後の実装と一致する。具体的には、h3 の公開 API (コンストラクタ引数・プロパティ・メソッド・Config プロパティ・Event フィールド・StreamInfo。値が設定される経路の無い `h3.Event.is_unidirectional` は除く) がすべて SKILL.md に記載され、記載された API がすべて実在し、シグネチャ (引数名・順序・デフォルト値・戻り値) が一致する (相互検証)。「注意点」の h3 関連記述の修正とサンプルコードの更新を含む
