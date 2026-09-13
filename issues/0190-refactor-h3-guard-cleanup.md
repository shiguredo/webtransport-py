# WebTransport over HTTP/3 のフレーム境界ガード残留を有界にする

- Created: 2026-09-07
- Completed: {YYYY-MM-DD}
- Branch: feature/refactor-h3-guard-cleanup
- Polished: {YYYY-MM-DD}

## 目的

0145 で導入したフレーム境界ガードの per-stream 状態が、閉鎖されないストリームに残り続けると長時間接続で蓄積する。除去経路を整えて残留を有界にする。

## 現状

- H3Session の headers_guards_ は receive_stream_data で生成され、除去は close_stream でのみ行う
- 403 拒否・非 CONNECT 確定・先頭非 HEADERS の WT データでは close_stream が呼ばれない経路があり残留する
- 同時並行数では有界だが lifetime total では無界になり得る既知の制約である
- 機能面の誤りは確認されていないためバグ修正ではなく整理である

## 設計方針

- ガード不要が確定したエントリの除去箇所を追加する。候補は stream_close_cb での除去、または投入済み非セッションエントリの除去である
- 除去は保持データの投入順序と FIN 検知を壊さない位置で行う
- 振る舞い変更を伴わないためイベント列に差異が出ないことをテストで確認する

## 完了条件

- ガード残留が有界になるか残留しない設計になり、既存テストが通過すること

## 調査結果 (2026-09-13)

実装を試みたが、有効な修正であることを検証できなかったため pending にする。

- 現状の把握: `H3Session::headers_guards_` のエントリは、クライアント起点双方向ストリーム (`stream_id % 4 == 0`) で `stream_info_` 未登録のものにデータが届いたときに生成される。CONNECT ストリームが該当するため、セッション確立時点で 1 件存在する (`_has_frame_guard` で確認済み)
- 除去経路: `H3Session::close_stream` が `nghttp3_conn_close_stream` の前後で `headers_guards_.erase` を呼ぶ。ストリーム終了 (ピアの RESET_STREAM・自側の close_stream・セッション終了) はすべてここを通るため、終了したストリームのガードは残らない
- 検証: セッション確立後に `close_stream(session_id, 0)` を呼ぶとガードが削除されることを白箱観測した。`H3Session::stream_close_cb` に除去を追加する案も試したが、`close_stream` 側で既に除去されるため追加の有無で観測結果が変わらず、回帰ピンとして成立しなかった
- 暫定結論: ガードが残留するのは「nghttp3 がストリーム終了を通知しないまま、ピアがそのストリームの送信を止める」場合に限られる。この場合のエントリ数は広告済みの同時ストリーム数で有界であり、lifetime total で無界になる経路は特定できなかった
- pending の理由: 無界に残留する再現手順が特定できないため、検証可能な修正にできない。同時ストリーム数の上限を超えて残留する経路が見つかった時点で reopened にする
