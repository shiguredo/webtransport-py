# skills/webtransport-py/SKILL.md の WebTransport over HTTP/3 の記述を最新化する

- Created: 2026-08-02
- Completed: YYYY-MM-DD
- Branch: feature/update-webtransport-skill
- Polished: 2026-08-04

## 目的

`skills/webtransport-py/SKILL.md` の WebTransport over HTTP/3 の記述を現在の実装と一致させる。スキルドキュメントが実装と食い違うと、LLM がライブラリを使う際に存在しない API を参照したり、実在する API を使い損ねたりする。

## 現状

`skills/webtransport-py/SKILL.md` の h3 セクション (asyncio API の h3 部分・Sans I/O の `h3.Session`・`h3.Config`・「注意点」の h3 関連記述。`h3.EventType` は現状の実装と一致しているため変更不要) が最新 API を反映していない:

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
- `h3.Server` / `h3.Client` のプロパティ一覧 (host / port / actual_port / is_running、url / host / port / is_connected / session_id) が SKILL.md に記載されていない (実装には存在する)

## 設計方針

- `skills/webtransport-py/SKILL.md` の h3 セクションのシグネチャとメソッド一覧を、`src/webtransport/h3/server.py`・`src/webtransport/h3/client.py`・`src/bindings/webtransport_h3.cpp` / `.h` の現在の実装に合わせて更新する。検証の参照元は C 拡張のバインディングと asyncio 実装とし、`src/webtransport/h3.pyi` はビルド時に nanobind が自動生成する成果物であり (CMakeLists.txt の nanobind_add_stub) git 追跡対象外のため参照元にしない
- `h3.Server` / `h3.Client` の `close_stream` は `reset_stream` に委譲する同一実装 (RESET_STREAM 送出) である旨を SKILL.md に注記する。`h3.Config.allowed_origins` は空リストで全オリジンを受理する旨も注記する (asyncio の `Server.allowed_origins` も None / 空リストで全オリジン受理)
- SKILL.md 内のサンプルコードも、シグネチャの変更反映が必要な箇所と、追加 API (`h3.Server.open_stream` 等) の使用例を反映して更新する
- `h3.Server.open_stream` はデフォルト単方向 (`unidirectional=True`)・双方向指定は `NotImplementedError`・失敗時 -1 を返す点を、シグネチャと注記で正確に記載する (session_id には `on_session_ready` で受け取った有効な値を渡す。draft-ietf-webtrans-http3-16 Section 4.3 の "can" に基づく任意実装。任意なのはサーバー起動の双方向ストリームの開始であり、双方向ストリームのサポート自体ではない)。「注意点」の「`h3` / `h2` は `unidirectional: bool = False` (どちらもデフォルトは双方向)」は、`h3.Client` / `h2` は双方向デフォルト・`h3.Server` のみ単方向デフォルトと書き分ける (asyncio API の話であり、デフォルト値を持たない Sans I/O の `h3.Session` / `h2.Session` の open_stream とは分けて記載する)
- `h3.Event.is_unidirectional` は値が設定される経路が無く常に False であるため、SKILL.md に記載する場合はその旨を注記する (値の設定は本 issue の対象外とする。必要になったら別 issue とする)。`h3.Client.open_stream` の失敗時挙動 (h3 層の登録失敗を無視して stream_id を返す) も実装上の非対称であり、SKILL.md には Server 側と対称に注記する (挙動の修正は本 issue の対象外とする)
- 他レイヤー (h2 / quic / http3 / http2) の API ドリフトの修正は対象外とする (「注意点」の unidirectional の書き分けで h2 の記述に触れる範囲は除く)。他レイヤーのドリフト (例: quic.Client の register_early_data / on_early_data_rejected の SKILL.md 未記載) は本 issue では扱わず、必要になったら別 issue として起票する
- 本 issue は 0009 / 0010 / 0017 の完了後に実施する (`close_stream` の戻り値が 0009 で、`close_stream` / `reset_stream` の挙動が 0010 で、`h3.Session` のストリーム状態 API が 0017 で変更されるため、先に書くとマージで再び古くなる)

## 完了条件

- `skills/webtransport-py/SKILL.md` の h3 セクションが 0009 / 0010 / 0017 のマージ後の実装と一致する。具体的には、h3 の公開 API (asyncio の `Server` / `Client` のコンストラクタ引数・メソッド・プロパティ、Sans I/O の `Session` の全メソッド・`Config` の全プロパティ・`Event` の全フィールド・`StreamInfo`。値が設定される経路の無い `h3.Event.is_unidirectional` と、`on_*` コールバック登録メソッド・テスト専用アクセサ `_has_stream_buffer` は除く) がすべて SKILL.md に記載され、記載された API がすべて実在し、シグネチャ (引数名・順序・デフォルト値・戻り値) が一致する (相互検証)。「注意点」の h3 関連記述の修正とサンプルコードの更新を含む
