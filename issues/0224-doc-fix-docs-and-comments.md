# 記述を実装に合わせる

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/update-fix-docs-and-comments
- Polished: {YYYY-MM-DD}

## 目的

利用者向けドキュメントとコードコメントの一部が実装と食い違っている。`skills/webtransport-py/SKILL.md` は利用者と LLM が最初に参照する仕様書であり、誤った契約を案内するとそのまま利用者コードの不具合になる。

## 現状

`SKILL.md`:

- `connect()` の説明が「`quic.Client` / `http2.Client` の `connect()` は `-> bool` で例外を送出しない」と書いているが、`src/webtransport/http2/client.py` の `Client.connect` は `asyncio.open_connection` を素で await するため `ConnectionRefusedError` / `ssl.SSLError` / `TimeoutError` を送出し、`False` を返す経路が無い
- 例外を送出するクライアントとして `h3` / `h2` しか挙げていないが、`src/webtransport/http3/client.py` の `Client.connect` も `ConnectTimeoutError` / `ConnectRefusedError` / `HandshakeFailedError` を送出する
- `http3.Client` のメソッド一覧に `connect` / `run` / `close` が無い。他の 4 モジュールは明記している
- `http3` のエラーコード定数の import 経路が書かれていない。`webtransport.http3` が再輸出するのは `H3_GENERAL_PROTOCOL_ERROR` のみである

`CHANGES.md`:

- `## develop` の「WebTransport over HTTP/3 と HTTP/2 の `Client.connect` を例外送出型に変更し、`bool` 戻り値を廃止する」という `[CHANGE]` は `h3` / `h2` しか挙げていないが、`http3.Client.connect` も同じ変更を受けている。`2026.1.0.dev0` 時点では `http3.Client.connect` は `-> bool` だった

`README.md`:

- 全サーバー例が `certfile="cert.pem"` / `keyfile="key.pem"` を要求するが、証明書の用意方法が書かれていない

コードコメント:

- `src/bindings/webtransport_h2.h` の `is_terminated` の説明が、実際にフラグが立つ条件 (サーバー側が `reject_session` に非 2xx を渡した場合ではなく 2xx を渡した場合) と食い違っている
- `tests/browser/conftest.py` に「h2 Server には Origin 検証が未実装」と書いたコメントが 2 箇所あるが、`src/webtransport/h2/server.py` の `Server` は `allowed_origins` を受け取り、`CHANGES.md` にも実装済みと記載されている
- `src/webtransport/_common.py` の `parse_wt_url` の docstring が「`https://` のスキームは大文字小文字を問わず除去しない」と自己矛盾した書き方になっている。実際は小文字の `https://` のみを除去する

## 設計方針

- 記述を実装に合わせる。実装を記述に合わせる変更は本 issue では行わない (必要なものは別 issue で扱う)
- `SKILL.md` は `tests/test_skill_api_consistency.py` が名前の存在しか検査していないため、説明文の誤りは人手で直す。再発防止として検査範囲を広げるかは別途判断する
- `CHANGES.md` は `shiguredo-changelog` の書式に従い、既存の `[CHANGE]` の記述を実装に合わせる

## 完了条件

- `SKILL.md` の `connect()` の説明、`http3.Client` のメソッド一覧、定数の import 経路が実装と一致する
- `CHANGES.md` の `[CHANGE]` に `http3.Client.connect` が含まれる
- `README.md` に証明書の用意方法が書かれている
- 上記のコードコメントが実装と一致する

## 解決方法

- `skills/webtransport-py/SKILL.md` の該当箇所を修正する
- `CHANGES.md` の `## develop` の該当行を修正する
- `README.md` のサーバー例の節に証明書の生成手順 (`examples/*/server.py` が案内している `openssl` のコマンド) を追加する
- `src/bindings/webtransport_h2.h`、`tests/browser/conftest.py`、`src/webtransport/_common.py` のコメントと docstring を実装に合わせて書き直す
