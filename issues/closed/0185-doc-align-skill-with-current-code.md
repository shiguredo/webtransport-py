# skills/webtransport-py/SKILL.md の記述を現コードと全面照合して不一致を解消する

- Created: 2026-09-07
- Completed: 2026-09-13
- Branch: feature/doc-align-skill-with-current-code
- Polished: {YYYY-MM-DD}

## 目的

SKILL.md (673 行) は本プロジェクトの利用者・LLM 双方が最初に参照する仕様書だが、現コードとの不一致が 13 件以上あり、コピー&ペースト例が動かないケース、実装に存在しない API の案内、実装にあるが SKILL に無い API の案内、死にフィールドを設定として提示している箇所などがある。正式リリース前に全面照合する。

## 現状

- 検出済みの不一致 (2 周目・3 周目のレビューで確認):
  1. SKILL:50 / :258 共通パターン「connect() 失敗は WebTransportConnectError 派生」→ `quic.Client.connect` / `http2.Client.connect` は `-> bool`
  2. SKILL:76 / :146 の `on_stream_reset(... error_code: int ...)` → 実装は `int | None` (issue 0182 と関連)
  3. SKILL:112 / :176 の `h3.Server.__init__` / `h3.Client.__init__` に `quic_config` が無い
  4. SKILL:193 / :213 の `h2.Server.__init__` / `h2.Client.__init__` に `config` が無い
  5. SKILL:196-200 の h2 サーバーコールバックに `on_session_request` / `on_error` が無い
  6. SKILL:213 「h2.Client のコールバックは h3.Client と同じ」→ h2.Client だけ `on_error` を持つ
  7. SKILL:325 「request() の形は http3 と同じ」→ http2 は `body: bytes | None = None` を持つ (CHANGES [CHANGE] あり)
  8. SKILL:403-430 の quic.Connection プロパティに `remote_reset_stream_at -> bool | None` が無い
  9. SKILL:459-503 の h3.Session に `map_send_error_code(stream_id, error_code) -> int` が無い
  10. SKILL:510 / :654 の h3.Event に `status_code` / `SESSION_REJECTED` が無い
  11. SKILL:518-557 の http3.Connection に `drained -> bool | None`、Client/Server の `reset_stream` が無い
  12. SKILL:603 `select_alpn(protocols: list[str])` → pyi:228 では `client_protocols`
  13. SKILL:607 / :642「h2.Config は http2.Config の項目に加えて」→ h2.Config に `send_preface` / `no_rfc7540_priorities` が無い
  14. SKILL:638 quic.Config 既定値一覧に `enable_reset_stream_at=True` と `max_stream_data_*=262144` が無い
  15. SKILL:641 死にフィールド `send_preface=True` を仕様として案内
  16. SKILL:661 「asyncio ラッパーの未接続時操作も RuntimeError」→ `run()` のみ RuntimeError、`send_*` / `open_stream` / `send_datagram` は無言 no-op か -1
  17. SKILL:662 「`ResponseWriter` は再エクスポートされていない」の broken window (issue 0184 で解消予定)
  18. SKILL:506 「reset_stream は close_stream を呼ぶだけ」→ 実装は `map_send_error_code` でリマップしてから close_stream
- 3.14 / 3.14t のみサポートの記述と shiguredo-python の「直近 3 系」の乖離 (別 issue で扱う)

## 設計方針

- 上記 18 件を一つずつ現コード (`src/webtransport/**/__init__.pyi`、`h3.pyi`、`h2.pyi`、`src/webtransport/**/*.py`) と照合し、SKILL.md を実装に合わせて更新する
- 実装が正 (SKILL が誤り) のケースがほとんどのため SKILL.md 側を修正する。ただし SKILL の記述が仕様として妥当で実装が追いついていない場合は別 issue に切り出す
- 3.14t / free-threading の条件 (issue 0148 と関連) も SKILL に明記する
- 修正後は「SKILL に載っている API と実装の API を機械的に照合する CI」の導入を検討する (別 issue 候補)

## 完了条件

- 上記 18 件の不一致がすべて解消していること
- SKILL のコード例がコピー&ペーストで動作すること
- 死にフィールドが仕様として案内されていないこと
- 既存のテスト全 822 件が引き続き通過すること

## 解決方法

実装が正であり SKILL.md が古いケースばかりだったため、SKILL.md 側を実装に合わせて更新した。起票後に解消済みだった項目 (on_stream_reset の `int | None`、`h2.Server` / `h2.Client` の `config`、死にフィールド `send_preface`、`ResponseWriter` の再エクスポート) は再確認のうえ変更していない。

- `connect()` の契約を層ごとに書き分けた。`h3.Client` / `h2.Client` は `-> None` で `WebTransportConnectError` 派生の具体例外を送出し、`quic.Client` / `http2.Client` は `-> bool` を返して例外を送出しない
- `h3.Server.__init__` に `quic_config`、`h3.Client.__init__` に `quic_config` と `close_wait_timeout` を追記した
- `h2.Server` の `on_session_request` / `on_error` / `on_goaway` と、`h2.Client` の `on_stop_sending` / `on_error` / `on_goaway` を追記した (h2.Client だけが持つ点も明記)
- 実装に存在しない `h3.Client.on_stop_sending` の案内を削除した
- `http2.ResponseWriter.drain` と `http2.Client.request` の `body` を追記した
- `quic.Connection.remote_reset_stream_at` を追記した
- `h3.Session.map_send_error_code` を追記し、「`reset_stream` は `close_stream` を呼ぶだけ」という記述を `WT_APPLICATION_ERROR` へのリマップの説明に直した
- `h3.Event.status_code` / `h3.EventType.SESSION_REJECTED` / `h2.EventType.GOAWAY` / `h2.Event.last_stream_id` を追記した
- `http3.Connection.drained` と `http3.Client` / `http3.Server` の `reset_stream` を追記した
- `http2.select_alpn` の引数名を `client_protocols` に直した
- `h2.Config` が `no_rfc7540_priorities` を持たないことを明記し、WebTransport 用 7 項目を列挙した
- `quic.Config` の既定値に `max_stream_data_bidi_local` / `max_stream_data_bidi_remote` / `max_stream_data_uni` / `enable_reset_stream_at` / `session_ticket` / `early_transport_params` を追記した
- asyncio ラッパーの未接続時操作の契約を「`run()` のみ `RuntimeError`、送信系は黙って破棄、`open_stream` は -1」に直した
- トップレベルパッケージの説明に例外クラスの再エクスポートを追記した

検証:

- SKILL.md から完全なコード例 (h3 サーバー / h3 クライアント) を機械的に抜き出し、自己署名証明書を置いた一時ディレクトリで相互接続させ、エコーと server 起点ストリームの受信まで動作することを確認した
- SKILL.md の API 参照を実装と突き合わせる一時スクリプトを書き、dotted 名の存在確認とシグネチャ一覧ブロックのメンバー確認の両方で不一致 0 件を確認した
- この機械照合は再発防止のためにテスト化する価値があるため、issue 0207 として起票した
- `uv run pytest tests/ -q --timeout=60` で 1119 件すべて通過することを確認した (SKILL.md のみの変更のため実装は無変更)
