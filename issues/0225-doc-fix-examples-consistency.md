# examples を実装と相互に整合させる

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/update-fix-examples-consistency
- Polished: 2026-09-15

## 目的

`skills/webtransport-py/SKILL.md` は「リポジトリの `examples/` に全モジュールの動くサンプルがある」と案内しているが、実行時に噛み合わない組と、単体で動かすと必ず失敗する例がある。利用者が最初に触るコードなので、出荷状態で動くようにする。

## 現状

応答を返せない組:

- `examples/http3/server.py` は `on_stream_end` の中でだけ応答する
- `examples/http3/client.py` は `Client.request("GET", "/")` の後にリクエストを終端しない。`Client.request` が終端しない件は別 issue (0219) で扱う
- 加えて接続先が一致していないため、現状は example server に到達しない。接続先を揃え、0219 の終端 (または 0219 が入るまでの暫定の終端呼び出し) が入って初めて組として応答が返る
- `examples/http3/client.py` の `asyncio.wait_for(client.run(), timeout=5.0)` は、`Client.run` が接続終了まで戻らないための設計どおりの終了経路である。`examples/http3/server.py` は応答後も接続を維持するため、修正後も 5 秒で `TimeoutError` になり `except TimeoutError: pass` が吸収する (他の 4 本の client 例も同じ形)。修正後は 5 秒以内に応答が届く

接続先と証明書検証の不一致:

- `examples/http2/client.py` は `www.google.com` の 443 番へ接続するが、`examples/http2/server.py` は 8443 番で待ち受ける
- `examples/http3/client.py` は `www.google.com` の 443 番へ接続するが、`examples/http3/server.py` は 4433 番で待ち受ける
- `examples/quic` / `examples/webtransport/h2` / `examples/webtransport/h3` は client と server の接続先が一致している (client は `localhost`、server は `0.0.0.0` で待ち受ける)
- `examples/http2/client.py` と `examples/http3/client.py` は `verify_peer` を指定せず既定の `True` になる。server 例は自己署名証明書の生成を案内しているため、そのままでは接続できない
- `http2.Client` は `ca_file` を引数に取らない (`verify_peer` のみ)。`http3.Client` は `ca_file` を取る

単体で失敗する例:

- `examples/quic/server.py` にだけ証明書生成の案内が無い。他の 4 本の server 例は同一文面で `openssl` のコマンドを案内している。`src/webtransport/_common.py` の `validate_cert_key_files` が起動時に `FileNotFoundError` を上げるため必ず踏む

同時起動できない例:

- QUIC 系の server 例 3 本が同じ 4433 番を使う (`examples/quic/server.py` / `examples/http3/server.py` / `examples/webtransport/h3_server.py`)。`examples/http2/server.py` と `examples/webtransport/h2_server.py` も 8443 番で重なる

版数の記載:

- `examples/webtransport/h2_client.py` のコメントが `draft-ietf-webtrans-http2` と版数なしで書かれている。実装側 (`src/` と `skills/`) の記載は版数付きで、正本は `refs/webtrans/draft-ietf-webtrans-http2-15.txt` である

## 設計方針

- client 例の接続先は `localhost`、ポートは server 例と同じ (http2 は 8443、http3 は 4433) に揃え、`verify_peer=False` を指定する
- `ca_file` の選択肢をコメントで示すのは `examples/http3/client.py` に限る。`http2.Client` は `ca_file` を取らないため、`examples/http2/client.py` は `verify_peer=False` のみとし、検証を有効にする経路が現状は無い旨をコメントに書く (ca_file 対応の追加は別 issue)
- http3 の組が応答を返せるようにするには終端が必要である。0219 が入った場合は client 例の終端追加は不要であり、本 issue は接続先と `verify_peer` の修正のみを行う。0219 が入らない場合は client 例に終端の呼び出しを追加して組が応答を返すことを確認し、0219 の完了時にその呼び出しを削除する (削除は 0219 側の作業とする)
- 証明書生成の案内は他の 4 本と同じ文面で `examples/quic/server.py` に追加する
- ポートの重複は注記で足りるため、案内コメントを追加する (QUIC 系 3 本と h2 系 2 本)
- draft の版数は `draft-ietf-webtrans-http2-15` と明記し、正本を `refs/webtrans/draft-ietf-webtrans-http2-15.txt` とする

## 完了条件

- `examples/http2` / `examples/http3` の client と server の接続先 (ホスト `localhost` とポート) が一致し、`verify_peer=False` により自己署名証明書でハンドシェイクが成立する (http2 の組は応答が返る)
- `examples/http3/client.py` と `examples/http3/server.py` の組が応答を返す。0219 が入っている場合は client 例の変更なしに成立し、0219 が入らない場合は暫定の終端呼び出し (0219 の完了時に削除する) により成立する
- `examples/quic/server.py` に証明書生成の案内がある
- QUIC 系 server 例 3 本と h2 系 server 例 2 本のポート重複が注記されている
- `examples/webtransport/h2_client.py` のコメントに `draft-ietf-webtrans-http2-15` が入る
- `ty` の型検査が通過する (`pyproject.toml` の `[tool.ty.src]` が `examples` を含む)

## 解決方法

- `examples/http2/client.py` / `examples/http3/client.py` の接続先を `localhost` と server と同じポートに変更し、`verify_peer=False` を指定する。`examples/http3/client.py` には `ca_file` の選択肢をコメントで示し、`examples/http2/client.py` には `ca_file` が無い旨を書く
- `examples/http3/client.py` に終端の呼び出しを追加する (0219 が入らない場合のみ。0219 の完了時に削除する)
- `examples/quic/server.py` に証明書生成の案内を追加する
- QUIC 系 server 例 3 本と h2 系 server 例 2 本にポート重複の注記を追加する
- `examples/webtransport/h2_client.py` のコメントに `draft-ietf-webtrans-http2-15` を追加する
- 実行検証は手動で行う (`tests/` に examples を実行するテストは無い)。リポジトリルートで server 例と同じ `openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes` を実行して証明書を用意し、server を起動してから client を起動する。ポートを共有する組は同時に起動できないため 1 組ずつ確認する
