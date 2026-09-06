# skills/webtransport-py/SKILL.md の記述を現コードと全面照合して不一致を解消する

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
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
