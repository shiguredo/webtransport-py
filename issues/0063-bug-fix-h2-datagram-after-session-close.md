# HTTP/2 で WT_CLOSE_SESSION 受信後に send_datagram がデータグラムを送出する

- Created: 2026-08-11
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-datagram-after-session-close
- Polished: {YYYY-MM-DD}

## 目的

WebTransport over HTTP/2 の `send_datagram` が、WT_CLOSE_SESSION 受信後 (セッション終了を学習した後) にデータグラムカプセルをワイヤへ送出してしまう問題を修正する。HTTP/3 側で対応済み (issue 0057) と同種の問題が、HTTP/2 側の WT_CLOSE_SESSION 受信経路に残っている。

## 現状

- `src/bindings/webtransport_h2.cpp` の `send_datagram` は `send_capsule(session_id, CapsuleType::Datagram, data)` を呼ぶ
- `send_capsule` はセッションの存在確認 (`get_wt_session`) も `is_established` の確認もせず、`http2_stream_buffers_` にカプセルを直接積む
- `handle_wt_close_session` は受信時に `is_established = false` にするが、ストリーム自体はピアが閉じるまで生存する
- Sans-IO 構成での実測結果:
  - WT_CLOSE_SESSION 受信後に `send_datagram` を呼ぶと、データグラムカプセルが実際にワイヤへ送出される (ストリームが生存しているため)
  - 未確立のセッション ID 宛ではストリームが存在しないため送出されない
  - ローカル `close_session` (WT_CLOSE_SESSION 送出 + END_STREAM) 後は送信側が半クローズのため送出されない
- セッション終了の定義は draft-ietf-webtrans-http2-15 Section 3.4 (CONNECT ストリームのクローズ)。WT_CLOSE_SESSION は終了前の通知であり、受信後は終了を学習した状態とみなせる

## 設計方針

- `send_datagram` (または `send_capsule`) の冒頭で、セッションの存在確認 (`get_wt_session`) と `is_established` の確認を行い、終了した・未確立のセッション ID への送信を黙って無視する
- HTTP/3 側 (issue 0057) の対応方針 (セッション管理集合のメンバーシップ確認) を参考にする
- 変更対象は `src/bindings/webtransport_h2.cpp` / `src/bindings/webtransport_h2.h`、テスト、`CHANGES.md` (## develop セクションへの [FIX] エントリ)

## 完了条件

- WT_CLOSE_SESSION 受信後に `send_datagram` を呼んでもデータグラムがワイヤに送出されない
- 生存セッションへの `send_datagram` は従来どおり送出される
- ローカル `close_session` 後と未確立 ID のケースは従来どおり送出されない (回帰確認)
- モックなしの Sans-IO テストで検証できる
