# http3.Client.request() がストリーム開設と送信登録の失敗を無視する

- Created: 2026-09-15
- Completed: 2026-09-18
- Branch: feature/fix-http3-client-request-failure-ignored
- Polished: 2026-09-18

## 目的

`src/webtransport/http3/client.py` の `Client.request` は、ストリームを開けなかった場合とリクエスト登録に失敗した場合に、失敗を呼び出し側へ伝えない。利用者は「リクエストを送った」と誤認したまま待機に入る。

## 現状

- `Client.request` は `self._quic_connection.open_stream(True)` の戻り値を検査せずに `Http3Connection.submit_request` へ渡し、その bool 戻り値も捨てて `stream_id` を返す
- ストリーム開設の失敗は `quic.Connection.open_stream` の契約で -1 として現れる。同時ストリーム数の上限に達した場合や接続クローズ直後は -1 が返る
- `Client.request` の docstring は戻り値を「ストリーム ID」とだけ説明しており、失敗時の値を書いていない
- 負の `stream_id` を `Http3Connection.submit_request` に渡しても即座に abort はしない (`src/bindings/http3.cpp` の `Http3Connection::submit_request` が `stream_id < 0` を弾いて false を返す) が、この防波堤は依存ライブラリの assert に到達させないためのものであり、失敗を利用者へ伝える手段にはなっていない
- `Http3Connection.submit_request` が false を返す時点で、nghttp3 側には当該ストリームが作られていない。`Http3Connection.close_stream` は `nghttp3_conn_close_stream` を呼ぶだけで既存ストリームの終了を伝える API であり、ngtcp2 (QUIC) 側へは何も送出しない

## 設計方針

- ストリーム開設に失敗した場合は `-1` を返して終了する
- `Http3Connection.submit_request` が false を返した場合も `-1` を返して終了する。開設済みの QUIC ストリームは低レベル `quic.Connection.reset_stream` でリセットしてから返す (`src/webtransport/h3/client.py` の `Client.open_stream` が h3 層の登録失敗時に行う処理と対称。リセットしないとローカルのストリーム状態が接続終了まで open のまま残る。`http3.Client.reset_stream` は nghttp3 側にも通知するが、ここでは nghttp3 側にストリームが無いため低レベル層だけを呼ぶ)
- nghttp3 側にストリームが作られていないため `Http3Connection.close_stream` は no-op であり、解放処理には使わない
- docstring の `Returns` に失敗時の値を明記する
- `skills/webtransport-py/SKILL.md` の `http3.Client` の `request()` にも失敗時の値を追記する
- 併せて「リクエストを終端しない」問題は別 issue (0219) で扱う。本 issue では失敗の伝播だけを扱い、0219 の `body` 追加後も失敗分岐を維持する。0219 も同じ `Client.request` と `tests/test_e2e_http3.py` を変更するため、本 issue を先に実装し、0219 で `body` の追加と終端を入れる

## 完了条件

- ストリーム上限到達時に `Client.request` が -1 を返す (`Client._quic_connection.streams_bidi_left` が 0 の状態で呼ぶ)
- `Http3Connection.submit_request` が false を返す場合に `Client.request` が -1 を返し、開設済みの QUIC ストリームが `Client._quic_connection.reset_stream` でリセットされる。低レベル `quic.Connection.reset_stream` は戻り値を返さないため、テストではサーバー側の `on_stream_reset` が当該 stream_id と error_code 0 で発火することを表明して観測する
- docstring と `skills/webtransport-py/SKILL.md` に失敗時の戻り値が記載される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/webtransport/http3/client.py` の `Client.request` で `quic.Connection.open_stream` の戻り値を検査し、負値 (`-1`) の場合はリクエストを登録せずに -1 を返すようにした (未接続の場合も従来どおり -1)
- `Http3Connection.submit_request` が false を返した場合は `Client._quic_connection.reset_stream(stream_id, 0)` で開設済みの QUIC ストリームをリセットしてから -1 を返すようにした。登録に失敗したままにするとローカルのストリーム状態が接続終了まで open のまま残る。`Http3Connection.close_stream` は nghttp3 に既存ストリームの終了を伝える API であり、nghttp3 がストリームを作る前に失敗する経路 (GOAWAY 受信・QPACK 未バインド) では通知すべき相手がいないため使わない (`src/webtransport/h3/client.py` の `Client.open_stream` の登録失敗時と同じ形)
- docstring の `Returns` に失敗時の戻り値 (-1) と失敗条件 (未接続・同時ストリーム数の上限到達・ハンドシェイク未完了・接続クローズ直後・登録失敗)、リセットの送出が `run()` の送信ループに委ねられることを明記し、`skills/webtransport-py/SKILL.md` の `request()` にも同じ内容を追記した
- `tests/test_e2e_http3.py` に 2 件追加した
  - `test_request_returns_minus_one_when_streams_exhausted`: サーバーが広告する双方向ストリーム上限 (`remote_initial_max_streams_bidi`) まで `Client.request` を呼び `streams_bidi_left` が 0 になったことを表明してから、次の呼び出しが -1 を返すことを確認する
  - `test_request_returns_minus_one_when_submit_request_fails`: サーバー側から GOAWAY を送出してクライアントに受信させ、`Client.request` が -1 を返すことと、開設済みの QUIC ストリームがリセットされたことをサーバー側の `on_stream_reset` (stream_id はクライアント起点の双方向、error_code は 0) で観測する。GOAWAY は 1 往復後に送出し、`-1` は期限付きで再試行して観測したうえで `streams_bidi_left > 0` を表明し、枯渇経路との取り違えを防ぐ
- 枯渇のテストは修正前実装でも -1 を返していたため回帰ガードにはならない (修正前は `submit_request(-1, ...)` の失敗を無視して -1 を返していた)。docstring に検出限界として明記した。回帰ガードは登録失敗のテストで、修正前実装では再試行が枯渇まで進んで失敗することを実測で確認した
- `CHANGES.md` の `## develop` にはエントリを追加していない。`CODEBASE.md` に「この指示がなくなるまでは変更履歴を `CHANGES.md` に残さないこと」という指示がある
- 本対応のスコープ外: `Client._setup_http3_streams` は QPACK 用単方向ストリームの開設失敗を検査しないため、QPACK 未バインドのまま `request()` が恒久的に -1 を返し、そのたびに双方向ストリームの累積枠を消費し得る (control ストリームの失敗では `ValueError` が `connect()` へ漏れる)。いずれも本 issue の対象外とする。リクエストの終端 (fin) は 0219 で扱う
