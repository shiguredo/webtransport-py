# http3.Client.request() がストリーム開設と送信登録の失敗を無視する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
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

- `src/webtransport/http3/client.py` の `Client.request` で `quic.Connection.open_stream` の戻り値と `Http3Connection.submit_request` の戻り値を検査する
- `submit_request` が false の場合は `Client._quic_connection.reset_stream(stream_id, 0)` を呼んでから -1 を返す
- `tests/test_e2e_http3.py` に 2 つのテストを追加する
  - ストリーム枯渇: サーバーが広告する双方向ストリーム上限 (既定 100) まで `Client.request` を呼び、次の呼び出しが -1 を返すことを確認する。上限到達は `quic.Connection.open_stream` の失敗として現れる。`Client._quic_connection.streams_bidi_left` が 0 であることを表明し、枯渇経路で -1 になったことを明示する
  - `submit_request` 失敗: サーバー側から GOAWAY を送出してクライアントに受信させ、`Client.request` が -1 を返すことを確認する (この経路では `open_stream` は成功し `submit_request` だけが `NGHTTP3_ERR_CONN_CLOSING` で false になる)。手順は次のとおりである
    - GOAWAY は `Client.request` を 1 回通して 1 往復させてから送出する。往復の完了前に送出すると GOAWAY がパケット化されずクライアントに届かない
    - 高レベル `http3.Server` に `goaway()` は無い。低レベル `http3.Connection.goaway()` を `Server._clients` の `ClientConnection.http3_connection` に対して呼び、`Server._send_to(addr, client)` で送出する (`tests/test_e2e_http3.py` の他のテストも `Server._clients` を直接参照している)。`goaway()` の時点で GOAWAY フレームは制御ストリームの送信データとして積まれる (`Server.run()` のループも同じ `Server._send_to` を呼ぶため、明示呼び出しは送出タイミングを確定させるために行う)。`goaway()` の後に `Http3Connection.get_streams_to_send()` を中身の確認のために呼ぶと、テスト側が GOAWAY のバイト列を取り出して消費してしまいピアへ届かないため、呼ばない
    - GOAWAY を送出しただけではクライアントの `run()` が受信を処理するまで `submit_request` は失敗しないため、`Client.request` が -1 を返すまで期限付きで再試行する (クライアント側に GOAWAY 受信を観測する公開 API は無く、固定 sleep では所要時間が読めない)
    - -1 を観測した時点で `Client._quic_connection.streams_bidi_left` が 0 より大きいことを表明する。この表明を呼び出し前に置くと、GOAWAY が届かないまま上限まで呼んで枯渇で -1 になった場合に表明が素通りし、テストが空振りで通る
