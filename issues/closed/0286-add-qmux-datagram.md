# QMux に DATAGRAM (Unreliable Datagram Extension) を追加する

- Created: 2026-10-14
- Completed: 2026-10-18
- Branch: feature/add-qmux-datagram
- Polished: {YYYY-MM-DD}
- Reporter: @voluntas

## 目的

draft-ietf-quic-qmux-02 Section 9.1 は Unreliable Datagram Extension (RFC 9221) の使用を許可しており、QMux 上でも DATAGRAM を載せられる。しかし webtransport-py の QMux 層は DATAGRAM を一切持たず、Sans-IO でも asyncio でも送受信できない。`webtransport.quic` / `webtransport.h2` / `webtransport.h3` は `send_datagram` と `on_datagram` を持つため、QMux だけが取り残されている。

## 現状

- dwnx の公開ヘッダ `dwnx/dwnx.h` (`deps.json` の ref `85c43b506e48f268029e98514d26fe65c8ff749e`、上流 main も同じ) に DATAGRAM フレームを送出する API も `max_datagram_frame_size` トランスポートパラメータも無い
- `webtransport_ext.qmux` の `Config` / `Connection` / `EventType` に DATAGRAM の項目は無く、`webtransport.qmux` の asyncio API にも `send_datagram` / `on_datagram` は無い
- 0279 の解決方法は「DATAGRAM は dwnx が未実装のため対応していない。CODEBASE.md の『ngtcp2 / nghttp3 / nghttp2 をフォークしない』方針に従い、上流の対応を待つ」と明記しており、0281 も DATAGRAM を対象外としている
- draft Section 9.1 は、DATAGRAM の最大長がピアの `max_datagram_frame_size` と `max_record_size` (Section 5.2) の両方で制約され、QMux では record がパケットに代わる容器になるため `max_record_size` が上限になると定めている。DATAGRAM フレームは record を跨げない
- record のパースは dwnx の内部にあり、バインディング側からフレームを覗く経路は無い。dwnx の対応なしに webtransport-py 側だけで受信経路を作ることはできない

## 設計方針

- 上流 dwnx が DATAGRAM を実装するまで着手しない。ngtcp2 / dwnx をフォークしない方針 (CODEBASE.md) に従う
- dwnx の対応が入ったら `deps.json` の ref を更新して取り込み、`QmuxConfig` に DATAGRAM の設定、`QmuxConnection` に `send_datagram` と受信イベント、`EventType` に DATAGRAM を追加する
- asyncio 層の `Client` と `Server` の `Session` は、`webtransport.quic` / `h2` / `h3` の `send_datagram` / `on_datagram` と引数と形を揃える
- DATAGRAM が record を跨がないことと、`max_record_size` がペイロード上限になることをテストで表明する
- テストはモックやスタブを使わず、2 つの接続をメモリ上のバイト列で直結して検証する (asyncio 層は実 TCP)

## 完了条件

- Sans-IO API と asyncio API の両方から DATAGRAM を送受信できる
- DATAGRAM が record を跨がず、`max_record_size` が上限になることがテストで表明されている
- モックやスタブを使わない実通信のテストと、`make develop` の型スタブ差分チェックと全テストが通る

## pending にした理由

- フレームの送出と record のパースの両方が dwnx の内部にあり、webtransport-py 側の変更だけでは完了できない外部依存のため
- dwnx 上流が DATAGRAM (RFC 9221) の送出 API と `max_datagram_frame_size` トランスポートパラメータを提供した時点で reopened にして設計方針を確定する。フォークしない方針が変わった場合も同様である

## reopened にした理由

- dwnx 上流 `aeb481c` が `max_datagram_frame_size` トランスポートパラメータ、`dwnx_conn_writev_datagram` / `dwnx_conn_write_datagram`、`dwnx_recv_datagram` コールバックを実装し、pending にした理由だった外部依存が解消したため
- `deps.json` の dwnx を `aeb481c` に更新して取り込んだため、設計方針を確定して実装に着手できる状態になった

## 解決方法

- dwnx の DATAGRAM 対応を取り込み、`webtransport_ext.qmux.Config.max_datagram_frame_size` で受信上限を広告する (`0` なら広告も送信もしない。既定値は `16382`)
- `webtransport_ext.qmux.Connection.send_datagram` を追加し、`dwnx_conn_writev_datagram` で record へ書く。ローカルまたはピアが DATAGRAM を広告していない場合、フレームが record を跨ぐ場合、ピアの上限を超える場合は黙って破棄し、ハンドシェイク前に予約された分は完了後に送る
- `dwnx_recv_datagram` コールバックで部分受信を組み立て、`EventType.DATAGRAM` のイベントとしてペイロード全体を 1 回だけ通知する。`remote_max_datagram_frame_size` と `local_max_datagram_frame_size` で上限を観測できる
- Sans-IO の `Connection.send_datagram` / `EventType.DATAGRAM` と、asyncio の `Client.send_datagram` / `Client.on_datagram` / `Server.on_datagram` / `Session.send_datagram` を追加し、quic 層と引数・形を揃えた
- record 境界 (最大長の DATAGRAM が record を埋め尽くし、最後の 1 バイトを渡すまでイベントが発火しないこと) と、record に収まらないデータグラムの破棄、ピア上限による破棄、分割 record の再組み立て、非対応ピア・無効設定を、モックなしのテストで表明した (Sans-IO は 2 接続のメモリ直結、asyncio は実 TCP)
- `make develop` の型スタブ差分チェックと全 1551 件のテストが通ることを確認した
