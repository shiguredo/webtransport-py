# examples を実装と相互に整合させる

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/update-fix-examples-consistency
- Polished: {YYYY-MM-DD}

## 目的

`skills/webtransport-py/SKILL.md` は「`examples/` に全モジュールの動くサンプルがある」と案内しているが、実行時に噛み合わない組と、単体で動かすと必ず失敗する例がある。利用者が最初に触るコードなので、出荷状態で動くようにする。

## 現状

応答を返せない組:

- `examples/http3/server.py` は `on_stream_end` の中でだけ応答する
- `examples/http3/client.py` は `Client.request("GET", "/")` の後にリクエストを終端しない。`Client.request` が終端しない件は別 issue で扱う
- このため server の `on_stream_end` が発火せず、client は 5 秒で待機を打ち切る

接続先と証明書検証の不一致:

- `examples/http2/client.py` は `www.google.com` の 443 番へ接続するが、`examples/http2/server.py` は 8443 番で待ち受ける
- `examples/http3/client.py` は `www.google.com` の 443 番へ接続するが、`examples/http3/server.py` は 4433 番で待ち受ける
- `examples/quic` / `examples/webtransport/h2` / `examples/webtransport/h3` は client と server の接続先が一致している
- `examples/http2/client.py` と `examples/http3/client.py` は `verify_peer` を指定せず既定の `True` になる。server 例は自己署名証明書の生成を案内しているため、そのままでは接続できない

単体で失敗する例:

- `examples/quic/server.py` にだけ証明書生成の案内が無い。他の 4 本の server 例は `openssl` のコマンドを案内している。`src/webtransport/_common.py` の `validate_cert_key_files` が起動時に `FileNotFoundError` を上げるため必ず踏む

同時起動できない例:

- QUIC 系の server 例 3 本が同じ 4433 番を使う (`examples/quic/server.py` / `examples/http3/server.py` / `examples/webtransport/h3_server.py`)

版数の記載:

- `examples/webtransport/h2_client.py` のコメントが `draft-ietf-webtrans-http2` と版数なしで書かれている。実装側のコメントは版数付きである

## 設計方針

- client 例の接続先を server 例の待ち受け先に揃え、`verify_peer=False` を指定する。証明書検証そのものを学ばせたい場合は `ca_file` を併記する選択肢をコメントで示す
- http3 の組が応答を返せるようにする修正は、別 issue (request の終端) の結果に依存する。request の終端が入った場合は client 例の変更は不要になるため、依存先の完了を待ってから確認する。入らない場合は client 例に終端の呼び出しを追加する
- 証明書生成の案内は 4 本の server 例と同じ文面で `examples/quic/server.py` に追加する
- ポートの重複は注記で足りるため、案内コメントを追加する

## 完了条件

- `examples/http3/client.py` と `examples/http3/server.py` の組が応答を返す
- `examples/http2` / `examples/http3` の client と server の接続先が一致し、自己署名証明書で接続できる
- `examples/quic/server.py` に証明書生成の案内がある
- QUIC 系 server 例のポート重複が注記されている
- `examples/webtransport/h2_client.py` のコメントに draft の版数が入る
- `ty` の型検査が通過する

## 解決方法

- `examples/http2/client.py` / `examples/http3/client.py` の接続先と `verify_peer` を修正する
- `examples/http3/client.py` に終端の呼び出しを追加する (別 issue の結果に応じて判断する)
- `examples/quic/server.py` に証明書生成の案内を追加する
- QUIC 系 server 例 3 本にポート重複の注記を追加する
- `examples/webtransport/h2_client.py` のコメントに版数を追加する
