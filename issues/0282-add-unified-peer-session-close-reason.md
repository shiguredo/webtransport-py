# 統一 API の Client と Session ハンドルにピアのセッション終了コードと理由が伝わらない

- Created: 2026-10-09
- Completed: {YYYY-MM-DD}
- Branch: feature/add-unified-peer-session-close-reason

## 目的

統一 API (`webtransport.Client` / `webtransport.Server` がコールバックへ渡す `Session`) で、ピア起点の WebTransport セッション終了の終了コードと理由をアプリが観測できるようにする。

MOQT を扱うアプリ (moqt-py) は、プロトコル違反などで終了したセッションの理由を peer 側で受け取れず、WT-H2 では peer 起点の終了が 0 / 空文字として扱われる。終了の事実だけでは、正常終了とプロトコル違反を区別できない。

## 現状

- `webtransport.Client.on_session_closed` のコールバックは `session_id` のみを受け取り、`webtransport.Server.on_session_closed` は `Session` ハンドルを渡す。どちらも終了コードと理由を読む口が無い
- 低レベルは理由を持っている。`src/bindings/webtransport_h2.cpp` の `H2Session::handle_wt_close_session` は WT_CLOSE_SESSION の Application Error Code とメッセージを `H2EventType::SessionClosed` に載せており、`_h2_client` はそれを `WebTransportSessionClosedError` として保持する。ただし送出は `run()` の終端だけで、終了コードが 0 の場合は例外自体が送出されない
- WT-H3 の CONNECT ストリーム終了経路では、`H3Session::close_stream` が `SESSION_CLOSED` の `error_message` を空で積む。この経路に理由は存在せず、コードのみが伝わる
- 影響: WT-H2 でピアが WT_CLOSE_SESSION で終了しても、統一 API からは終了コードと理由が観測できず 0 / 空文字になる
- 関連: 0268 が h2 層の高レベル API を対象に同種の対応を扱っている。h3 層の受信側は現在どの open issue も対象にしていない。本 issue は統一 API 層 (h2 / h3 の両方) を対象にする

## 設計方針

- 終了コードと理由を統一 API で観測できる形を決める。候補は (a) `on_session_closed` のコールバック引数に `error_code` / `error_message` を追加する、(b) 最後の終了理由を `Client` / `Session` ハンドルのプロパティとして公開する、(c) 終了理由専用のコールバックを追加する
- 0268 (h2 層) の設計と形を揃える。0268 が先に入る場合はその形に合わせる
- WT-H3 の CONNECT ストリーム終了経路では理由が空になり得るため、理由が無いこと自体を区別できる形にする (空文字と「理由未取得」を混同しない)
- ピアの WT_CLOSE_SESSION 由来か、ローカル検知の異常終了か、接続終了かを区別できるようにする
- 変更対象: `src/webtransport/client.py` / `server.py`、`src/webtransport/_h2_client.py` / `_h3_client.py` / `_h2_server.py` / `_h3_server.py`、`skills/webtransport-py/SKILL.md`、追加 API のテスト (実通信でピアの値まで観測する)
- 対象外: h2 層 / h3 層を直接使う利用者向けの API (h2 層は 0268、h3 層は別途)

## 完了条件

- WT-H2 でピアが `WT_CLOSE_SESSION(error_code, error_message)` を送ったとき、統一 API から同じ終了コードと理由が観測できる
- WT-H3 の CONNECT ストリーム終了経路でも終了コードが観測でき、理由が空であることが分かる
- ローカル検知の異常終了とピア起点の終了を区別できる
- 既存の使い方 (コールバック引数の互換) を壊す場合は `skills/webtransport-py/SKILL.md` とテストを合わせて更新する
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する
