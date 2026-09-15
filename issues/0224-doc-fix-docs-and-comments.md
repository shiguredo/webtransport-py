# 記述を実装に合わせる

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/update-fix-docs-and-comments
- Polished: 2026-09-15

## 目的

利用者向けドキュメントとコードコメントの一部が実装と食い違っている。`skills/webtransport-py/SKILL.md` は利用者と LLM が最初に参照する仕様書であり、誤った契約を案内するとそのまま利用者コードの不具合になる。

## 現状

`SKILL.md`:

- `connect()` の説明が「`quic.Client` / `http2.Client` の `connect()` は `-> bool` で例外を送出しない」と書いている。`quic.Client` は接続失敗を `False` で通知するが、再入時は `RuntimeError` を送出する。`src/webtransport/http2/client.py` の `Client.connect` は `asyncio.open_connection` を素で await するため `False` を返す経路が無く、失敗時は `ConnectionRefusedError` / `ssl.SSLError` / `TimeoutError` が伝播する
- 同じ記述が 2 箇所ある。asyncio API の共通パターンの節と、独自例外クラスの注意点の節である。後者は `h3` / `h2` しか例外送出として挙げておらず、`http3.Client.connect` も `ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError` を送出する
- `http3.Client` のメソッド一覧に `connect` / `run` / `close` が無い。`quic.Client` と `h3.Client` は一覧に明記し、`h2.Client` は `h3.Client` と同形と散文で言及しているが、`http2.Client` はどのメソッドも列挙していない
- `http3` のエラーコード定数の import 経路が書かれていない。`webtransport.http3` が再輸出するのは `H3_GENERAL_PROTOCOL_ERROR` のみである (再輸出を増やす対応は別 issue)

`CHANGES.md`:

- `## develop` の「WebTransport over HTTP/3 と HTTP/2 の `Client.connect` を例外送出型に変更し、`bool` 戻り値を廃止する」という `[CHANGE]` は `h3` / `h2` しか挙げていないが、`http3.Client.connect` も同じ変更を受けている。`2026.1.0.dev0` 時点では `http3.Client.connect` は `-> bool` だった

`README.md`:

- 全サーバー例が `certfile="cert.pem"` / `keyfile="key.pem"` を要求するが、証明書の用意方法が書かれていない

コードコメント:

- `tests/browser/conftest.py` に「h2 Server には Origin 検証が未実装」と書いたコメントが 2 箇所あるが、`src/webtransport/h2/server.py` の `Server` は `allowed_origins` を受け取り、`CHANGES.md` にも実装済みと記載されている
- `src/webtransport/_common.py` の `parse_wt_url` の docstring が「`https://` のスキームは大文字小文字を問わず除去しない」と自己矛盾した書き方になっている。実装は `str.replace` で `https://` の出現をすべて除去する

## 設計方針

- 記述を実装に合わせる。実装を記述に合わせる変更は本 issue では行わない (必要なものは別 issue で扱う)
- `SKILL.md` は `tests/test_skill_api_consistency.py` が名前の存在しか検査していないため、説明文の誤りは人手で直す。再発防止として検査範囲を広げるかは別途判断する
- `CHANGES.md` は `shiguredo-changelog` の書式に従い、既存の `[CHANGE]` の記述を実装に合わせる
- 同じ記述を別 issue が変更する予定があるため、実装順に依存しない書き方にする
  - `connect()` の再入契約と `http3.Client` のメソッド一覧は別 issue (0218) が同じ節を更新する。0218 が先に入った場合は `connect` の記載が既にあるため、`run` / `close` の欠落と説明文の誤りだけを対象とする
  - `http3` の定数の import 経路は別 issue (0220) が再輸出を追加する。0220 が先に入った場合は `webtransport.http3` からの経路を、入っていない場合は `webtransport.http3.constants` からの経路を書く
  - `http3.Client.request` の契約は別 issue (0219) が同じコードブロックを更新する。0219 が入った後の記述に合わせる

## 完了条件

- `SKILL.md` の `connect()` の説明が層ごとの実装と一致する (`quic.Client` は接続失敗を `False`、再入は `RuntimeError`。`http2.Client` は接続失敗も例外。`h3` / `http3` / `h2` は例外送出型)
- `SKILL.md` の `http3.Client` のメソッド一覧に `run` / `close` がある (0218 が先に入った場合は `connect` も)
- `SKILL.md` に `http3` のエラーコード定数の import 経路が書かれている
- `SKILL.md` の `http2.Client` の節の扱いを決め、`connect` / `run` / `close` を記載するか対象外である旨を書く
- `CHANGES.md` の `[CHANGE]` に `http3.Client.connect` が含まれる
- `README.md` に証明書の用意方法が書かれている
- 上記のコードコメントが実装と一致する

## 解決方法

- `skills/webtransport-py/SKILL.md` の該当箇所 (asyncio API の共通パターンの節、独自例外クラスの注意点の節、`http3.Client` と `http2.Client` の節) を修正する
- `CHANGES.md` の `## develop` の該当行を修正する
- `README.md` のサーバー例の節に証明書の生成手順 (`examples/*/server.py` が案内している `openssl` のコマンド) を追加する
- `tests/browser/conftest.py` と `src/webtransport/_common.py` のコメントと docstring を実装に合わせて書き直す
