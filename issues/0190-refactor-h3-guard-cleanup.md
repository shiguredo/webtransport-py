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
