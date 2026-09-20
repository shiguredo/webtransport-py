# http3 の高レベル層がピアの RESET_STREAM を nghttp3 へ伝えない

- Created: 2026-09-20
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-http3-peer-reset-not-forwarded
- Polished: {YYYY-MM-DD}

## 目的

`src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` は、QUIC の `STREAM_RESET` を受信するとアプリのコールバック (`on_stream_reset`) を呼ぶだけで、低レベルの `Http3Connection` に読み取り中断を伝えない。そのため nghttp3 はリセットされたストリームの受信側を生存として扱い続け、`Http3Connection::pending_headers_` (受信途中のヘッダーブロック) が接続終了まで残る。ピアが「部分的な HEADERS + RESET_STREAM」を繰り返すと、ストリームを張り替えるたびにエントリが積み上がる。

## 現状

- `src/webtransport/http3/client.py` と `src/webtransport/http3/server.py` の `STREAM_RESET` 分岐は `self._on_stream_reset(...)` を呼ぶだけである
- `Http3Connection.reset_stream` の呼び出し元は `Client.reset_stream` と `Server.reset_stream` のみで、どちらもアプリが明示的に呼ぶ公開 API である。アプリがピアのリセットを受けて応答として `reset_stream` を呼んだ場合にのみ到達し、呼ばない限り到達しない
- `Http3Connection.close_stream` は `src/webtransport/http3/` からは呼ばれていない (残りの DATA イベントを落とすため、QUIC FIN による終端検知では `nghttp3_conn_close_stream` を意図的に避けている。ピアの `RESET_STREAM` を受けた経路に同じ理由が当てはまるかは未確認)
- `Http3Connection` に読み取り中断だけを行う API は無い。`Http3Connection.reset_stream` は `nghttp3_conn_shutdown_stream_read` (受信側の中断。RFC 9114 Section 4.1.1 の「aborts reading on the receiving parts of streams」) と `ResetStream` イベントの push を同時に行う
- `Http3Connection::pending_headers_` の解放は 3 経路 (`reset_stream` / `stream_close_cb` / `reset_stream_cb`) に追加済みである。ただし高レベル層がリセットを転送しないため、ピア起点のリセットではこの解放に到達しない
- `ResetStream` イベントは高レベル層で無条件に QUIC の `RESET_STREAM` 送出に変換される。そのため転送に `Http3Connection.reset_stream` をそのまま使うと、ピアが既にリセットしたストリームへ `RESET_STREAM` を返送することになる
- WebTransport over HTTP/3 の本番経路 (`src/webtransport/h3/`) は `H3Session` を使い `Http3Connection` を生成しないため、本 issue の影響は素の HTTP/3 の利用者に限られる

## 設計方針

- 第一候補は「読み取り中断のみを行い、イベントを push しない `Http3Connection` のメソッド」を追加し、`STREAM_RESET` の受信時に呼ぶ案とする。`ResetStream` イベントを push しないため、ピアが既にリセットしたストリームへ `RESET_STREAM` を返送しない。メソッド内では `nghttp3_conn_shutdown_stream_read` を呼んだうえで受信途中のヘッダーブロックのエントリを解放する (読み取り中断だけではエントリは解放されない)。入力検証は既存の `Http3Connection.reset_stream` (ストリーム ID の範囲検査と閉鎖時の no-op) と同じ契約に揃える
- 代替案は `Http3Connection.close_stream` を呼ぶ案である。nghttp3 のストリーム自体が削除され `StreamEnd` イベントのみが push されるため返送は起きないが、nghttp3 のストリームが消えることで残りの DATA イベントが落ちる可能性がある。ピアの `RESET_STREAM` 後は DATA が届かないため実害は無いと見込まれるが、確認が必要である
- どちらの案でも、読み取り中断 (第一候補は `nghttp3_conn_shutdown_stream_read`、代替案は `nghttp3_conn_close_stream` によるストリーム削除) と、`pending_headers_` のエントリの解放が行われることを必須とする
- アプリの `on_stream_reset` コールバックの呼び出しと引数は変えない (転送はコールバックの前後どちらでもよい)

## 完了条件

- ピアから `STREAM_RESET` を受信したときに、低レベルの `Http3Connection` へ読み取り中断が伝わる
- 「部分的な HEADERS を送ったあとピアが `RESET_STREAM` を送る」状況を実 QUIC のピアで再現し、`Http3Connection::pending_headers_` のエントリが解放されることを検証する。解放の観測には `_has_pending_headers` を使う (この API は受信途中のヘッダーブロックの解放を扱う別 issue で追加されるテスト専用 API であり、それが先にマージされている前提で着手する)。ピアは低レベル QUIC 接続を使い、部分的な HEADERS フレームを注入したあと `RESET_STREAM` を送る。解放は `_has_pending_headers` で観測する。高レベル層と実 QUIC ピアで再現できない場合は、この条件を満たすテスト手段を先に定めてから実装する (手段が無いまま「`reset_stream` を呼ぶだけ」のテストを書かない)
- ピアが既にリセットしたストリームへ `RESET_STREAM` を返送しないことを、ピア側の QUIC 接続が受け取るイベント (ストリームのリセット通知) で検証する
- アプリの `on_stream_reset` コールバックが従来どおり呼ばれることを検証する
- 全テストが通過する

## 解決方法

- 未着手
