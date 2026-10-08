# h2 で空の WT_STREAM が送出され保留キューが恒久停止する

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-empty-wt-stream-stall
- Polished: 2026-10-08

## 目的

WebTransport over HTTP/2 で、アプリが FIN なしの空データを送ると、保留が無ければ仕様違反の空 WT_STREAM capsule が送出され、保留があれば空エントリが保留キューに積まれて当該ストリームの後続送出が恒久停止する問題を修正する。

draft-ietf-webtrans-http2-15 Section 6.4 は「Empty WT_STREAM capsules MUST NOT be used unless they open or close a stream; an endpoint MAY treat an empty WT_STREAM capsule that neither starts nor ends a stream as a session error.」と定める。空 WT_STREAM の送出は MUST NOT 違反であり、厳格なピアはセッションエラーにし得る。加えて本実装では、保留キューに積まれた空エントリが送出待ちの先頭に残り続けるため、そのストリームの後続データが永久に送出されない (FIN 付きの空データにも同じ経路の停止が生じる)。

## 現状

- `src/bindings/webtransport_h2.cpp` の `H2Session::open_stream` は「空の WT_STREAM capsule は送信しない」と明記して空送出を避けているのに対し、`H2Session::send_stream_data` は保留判定 `if (sendable < data.size())` が data 空で成立せず (0 < 0 が偽)、`encode_varint(stream_id)` だけをペイロードとする `CapsuleType::WtStream` の送出に到達する
- 高レベル `h2.Client.send_stream_data` (`src/webtransport/h2/client.py`) と `h2.Server` の `SessionWriter.send_stream_data` (`src/webtransport/h2/server.py`) は空データを弾かない。`bindings::check_python_input_size` にも下限の検査が無いため、`send_stream_data(stream_id, b"")` で Python から到達できる
- 保留 (`stream_info.pending_sends` が非空) がある状態で空データを送ると、`H2Session::send_stream_data` は順序維持のため空エントリを末尾に積む。`H2Session::flush_pending_sends` は先頭エントリの `sendable` をセッション残量 / ストリーム残量 / `pending.data.size()` の最小値で求めるため空エントリでは 0 になり、`if (sendable == 0) { break; }` で停止する。クレジットが増えても空エントリは消費されないため、そのストリームの後続データは送出されない (`H2Session::reset_stream` の `pending_sends.clear()` 以外に回復手段が無い)。FIN 付きの空データも保留がある場合は同じ保留経路を通るため、空 WT_STREAM_FIN も届かずに停止する (`flush_pending_sends` が空エントリを送出できないため、FIN エントリ送出後の `DataSent` 遷移も起きない)
- 既存テストの空データ送信は `b"", fin=True` のみである (`tests/test_webtransport_h2_send_stream_data_reset_stream.py`、`tests/test_webtransport_h2_received_map_bound.py`)。どちらも保留なしの直接送出経路であり、FIN なしの空送信と保留ありの空エントリ (FIN の有無を問わず) は未カバーである
- 影響: 仕様違反の capsule 送出と、アプリから見て原因不明の送信停止が起きる。どちらもローカルで再現できる

## 設計方針

- `H2Session::send_stream_data` の保留分岐より前に、FIN なしの空データを何もせず return する判定を置く。空 WT_STREAM は仕様上「ストリームを開く / 閉じる」場合にのみ許されるが、本実装は開設 (`open_stream`) と終端 (`fin` 付き送信) がそれぞれを担うため、空データ単独で表現すべき操作が無い
- FIN 付きの空データは「ストリームを閉じる」操作なので、従来どおり `CapsuleType::WtStreamFin` を送出する (ペイロードは Stream ID のみになるが Section 6.4 の close に該当する)。保留があり順序維持のために保留末尾へ空エントリを積む場合は、`H2Session::flush_pending_sends` が 0 バイトエントリも送出できるようにする (現状は `sendable == 0` で break され空 WT_STREAM_FIN が届かず、FIN なしと同じ恒久停止になる。0 バイトエントリはフロー制御クレジットを消費しない)
- 保留キューに空エントリが積まれる経路を塞ぐことを回帰テストで固定する (先にクレジットを枯渇させて保留を作り、空データを送り、クレジット到着後に後続データが届くことまで検証する)。FIN 付きの空データも保留のあるストリームで同じ形で検証する (クレジット到着後に空 WT_STREAM_FIN が届き、保留が残らないこと)
- `send_stream_data` の docstring (`src/bindings/webtransport_h2.h` の宣言コメントと `src/bindings/webtransport_h2.cpp` の binding docstring) と `skills/webtransport-py/SKILL.md` の h2 節に「FIN なしの空データは送出しない」を明記する。高レベル `h2.Client.send_stream_data` / `h2.Server` の `SessionWriter.send_stream_data` の docstring も同じ旨を追加する
- 変更対象: `src/bindings/webtransport_h2.cpp`、`src/bindings/webtransport_h2.h`、`src/webtransport/h2/client.py`、`src/webtransport/h2/server.py`、`skills/webtransport-py/SKILL.md`、`tests/`

## 完了条件

- `h2.Client.send_stream_data(stream_id, b"")` と `h2.Server` の `SessionWriter.send_stream_data(stream_id, b"")` が WT_STREAM を送出しない (低レベル `h2.Session.send_stream_data` も同じ。ピア側で capsule が観測されない)
- クレジット枯渇で保留が生じているストリームへ空データを送っても保留キューが詰まらず、クレジット到着後に後続データが欠落なく届く
- `b"", fin=True` は従来どおり WT_STREAM_FIN として送出され、ピア側でストリームの終端として観測できる (保留がある場合も、保留データの送出後に空 WT_STREAM_FIN が届く)
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する

## 解決方法
