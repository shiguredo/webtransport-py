# http3.Client.request() がストリーム開設と送信登録の失敗を無視する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-client-request-failure-ignored
- Polished: 2026-09-15

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

- ストリーム上限到達時に `Client.request` が -1 を返す
- `Http3Connection.submit_request` が false を返す場合に `Client.request` が -1 を返し、開設済みの QUIC ストリームが `Client._quic_connection.reset_stream` でリセットされる
- docstring と `skills/webtransport-py/SKILL.md` に失敗時の戻り値が記載される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/webtransport/http3/client.py` の `Client.request` で `quic.Connection.open_stream` の戻り値と `Http3Connection.submit_request` の戻り値を検査する
- `submit_request` が false の場合は `Client._quic_connection.reset_stream(stream_id, 0)` を呼んでから -1 を返す
- `tests/test_e2e_http3.py` に 2 つのテストを追加する
  - ストリーム枯渇: サーバーが広告する双方向ストリーム上限 (既定 100) まで `Client.request` を呼び、次の呼び出しが -1 を返すことを確認する。上限到達は `quic.Connection.open_stream` の失敗として現れる
  - `submit_request` 失敗: サーバー側の `http3.Connection.goaway` を送出してクライアントに GOAWAY を受信させた後、`Client.request` が -1 を返すことを確認する (この経路では `open_stream` は成功し `submit_request` だけが `NGHTTP3_ERR_CONN_CLOSING` で false になる)
