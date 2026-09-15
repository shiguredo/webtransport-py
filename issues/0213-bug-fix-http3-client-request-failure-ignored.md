# http3.Client.request() がストリーム開設と送信登録の失敗を無視する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-client-request-failure-ignored
- Polished: {YYYY-MM-DD}

## 目的

`src/webtransport/http3/client.py` の `Client.request` は、ストリームを開けなかった場合とリクエスト登録に失敗した場合に、失敗を呼び出し側へ伝えない。利用者は「リクエストを送った」と誤認したまま待機に入る。

## 現状

- `Client.request` は `self._quic_connection.open_stream(True)` の戻り値を検査せずに `Http3Connection.submit_request` へ渡し、その bool 戻り値も捨てて `stream_id` を返す
- `src/webtransport/http3/client.py` の `Client.open_stream` は失敗時に -1 を返す契約であり、同時ストリーム数の上限に達した場合や接続クローズ直後は -1 が返る
- `Client.request` の docstring は戻り値を「ストリーム ID」とだけ説明しており、失敗時の値を書いていない
- 負の `stream_id` を `Http3Connection.submit_request` に渡しても即座に abort はしない (`src/bindings/http3.cpp` の `Http3Connection::submit_request` が `stream_id < 0` を弾いて false を返す) が、この防波堤は依存ライブラリの assert に到達させないためのものであり、失敗を利用者へ伝える手段にはなっていない

## 設計方針

- ストリーム開設に失敗した場合は `-1` を返して終了する
- `Http3Connection.submit_request` が false を返した場合も `-1` を返して終了する。開設済みのストリームは `Http3Connection.close_stream` で解放してから返す
- docstring の `Returns` に失敗時の値を明記する
- 併せて「リクエストを終端しない」問題は別 issue で扱う。本 issue では失敗の伝播だけを扱う

## 完了条件

- ストリーム上限到達時と `submit_request` 失敗時に `Client.request` が -1 を返す
- docstring に失敗時の戻り値が記載される
- 上記を検証するテストが追加され、全テストが通過する

## 解決方法

- `src/webtransport/http3/client.py` の `Client.request` で `open_stream` の戻り値と `Http3Connection.submit_request` の戻り値を検査する
- 失敗時は開設済みストリームを `Http3Connection.close_stream` で解放する
- `tests/test_e2e_http3.py` に、同時ストリーム数を枯渇させた状態での `Client.request` が -1 を返すことを確認するテストを追加する
