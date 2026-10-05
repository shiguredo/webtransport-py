# 記述を実装に合わせる

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/update-fix-docs-and-comments
- Polished: 2026-09-15
- Updated: 2026-10-05

## 目的

利用者向けドキュメントとコードコメントの一部が実装と食い違っている。`skills/webtransport-py/SKILL.md` は利用者と LLM が最初に参照する仕様書であり、誤った契約を案内するとそのまま利用者コードの不具合になる。

## 現状

`SKILL.md`:

- `connect()` の説明が「`quic.Client` / `http2.Client` の `connect()` は `-> bool` で接続失敗を例外にしない (誤用である再入は `RuntimeError` になる)。`http2.Client` は成功時に `True` を返す (TCP + TLS の確立のみを見る)」と書いている (0218 で改稿済み)。`quic.Client` は接続失敗を `False` で通知するが、`src/webtransport/http2/client.py` の `Client.connect` は `asyncio.open_connection` を素で await するため `False` を返す経路が無く、失敗時は `ConnectionRefusedError` / `ssl.SSLError` / `TimeoutError` が伝播する
- 同じ記述が 2 箇所ある。asyncio API の共通パターンの節と、独自例外クラスの注意点の節である。前者の `h3.Client` / `h2.Client` の列挙と後者の列挙はいずれも `http3.Client` を挙げておらず、`http3.Client.connect` も `ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError` を送出する
- `http3.Client` のメソッド一覧に `connect` / `run` / `close` が無い。`quic.Client` と `h3.Client` は一覧に明記し、`h2.Client` は `h3.Client` と同形と散文で言及しているが、`http2.Client` はどのメソッドも列挙していない
- `http3` のエラーコード定数の import 経路が書かれていない。`webtransport.http3` が再輸出するのは `H3_GENERAL_PROTOCOL_ERROR` のみである (再輸出を増やす対応は別 issue)

`README.md`:

- 全サーバー例が `certfile="cert.pem"` / `keyfile="key.pem"` を要求するが、証明書の用意方法が書かれていない

コードコメント:

- `tests/browser/conftest.py` に「h2 Server には Origin 検証が未実装」と書いたコメントが 2 箇所あるが、`src/webtransport/h2/server.py` の `Server` は `allowed_origins` を受け取り、`src/bindings/webtransport_h2.cpp` で検証している
- `src/webtransport/_common.py` の `parse_wt_url` の docstring が「`https://` のスキームは大文字小文字を問わず除去しない」と自己矛盾した書き方になっている。実装は `str.replace` で `https://` の出現をすべて除去する

## 設計方針

- 記述を実装に合わせる。実装を記述に合わせる変更は本 issue では行わない (必要なものは別 issue で扱う)
- `SKILL.md` は `tests/test_skill_api_consistency.py` が名前の存在しか検査していないため、説明文の誤りは人手で直す。再発防止として検査範囲を広げるかは別途判断する
- `CHANGES.md` は変更しない (`CODEBASE.md` の「変更履歴を `CHANGES.md` に残さないこと」に従う。`http3.Client.connect` の例外送出型への変更は実装済みだが、変更履歴には追記しない)
- 同じ記述を別 issue が変更する予定があるため、実装順に依存しない書き方にする
  - `connect()` の再入契約は 0218 (完了済み) が同じ節を散文として更新済みである。`http3.Client` のメソッド一覧 (コードブロック) には `connect` / `run` / `close` が無いため、一覧の欠落と説明文の誤りを本 issue の対象とする
  - `http3` の定数の import 経路は別 issue (0220) が再輸出を追加する。0220 は未実装であるため、現行の `webtransport.http3.constants` からの経路を書く (0220 の実装後は再輸出の経路も併記する)
  - `http3.Client.request` の契約は 0219 (完了済み) が同じコードブロックを fin 終端の記述に更新済みである。その記述を前提とする

## 完了条件

- `SKILL.md` の `connect()` の説明が層ごとの実装と一致する (`quic.Client` は接続失敗を `False`、再入は `RuntimeError`。`http2.Client` は接続失敗も例外。`h3` / `http3` / `h2` は例外送出型)
- `SKILL.md` の `http3.Client` のメソッド一覧に `run` / `close` がある (`connect` の再入契約は 0218 の完了により散文に記載済み)
- `SKILL.md` に `http3` のエラーコード定数の import 経路が書かれている
- `SKILL.md` の `http2.Client` の節の扱いを決め、`connect` / `run` / `close` を記載するか対象外である旨を書く
- `README.md` に証明書の用意方法が書かれている
- 上記のコードコメントが実装と一致する

## 解決方法

- `skills/webtransport-py/SKILL.md` の該当箇所 (asyncio API の共通パターンの節、独自例外クラスの注意点の節、`http3.Client` と `http2.Client` の節) を修正する
- `README.md` のサーバー例の節に証明書の生成手順 (`examples/http2/server.py` / `examples/http3/server.py` / `examples/webtransport/h2_server.py` / `examples/webtransport/h3_server.py` が案内している `openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes`) を追加する
- `tests/browser/conftest.py` と `src/webtransport/_common.py` のコメントと docstring を実装に合わせて書き直す
