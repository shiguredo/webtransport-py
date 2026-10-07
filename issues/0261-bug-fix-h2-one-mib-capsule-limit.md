# h2 で 1 MiB ちょうどの送信が受信側のカプセル上限超過でセッションを閉じる

- Created: 2026-10-08
- Completed: {YYYY-MM-DD}
- Branch: feature/fix-h2-one-mib-capsule-limit

## 目的

WebTransport over HTTP/2 で、アプリの 1 回の送信が WT_STREAM capsule のペイロード上限を超えて、受信側が WT_ERROR でセッションを閉じる境界値の不整合を修正する。アプリの入力上限と capsule ペイロード上限が一致しているという前提 (`static_assert`) が、Stream ID の varint 分だけ崩れている。

## 現状

- `src/bindings/python_input.h` の `bindings::check_python_input_size` は `size > kMaxPythonInputBytes` (1 MiB) のみを拒否するため、1 MiB ちょうどの入力が Python から通る
- `src/bindings/webtransport_h2.cpp` の `H2Session::send_stream_data` は WT_STREAM capsule のペイロードを `encode_varint(stream_id)` とデータの連結で組み立てる (保留分の部分送出も同様)。このためペイロード長は「入力長 + Stream ID の varint 長 (1〜8 バイト)」になる
- `bindings::kMaxPythonInputBytes == H2SessionConfig{}.wt_max_capsule_payload_size` を保証する `static_assert` は「アプリの入力上限 = capsule ペイロード上限」を意図しているが、上記の varint 分だけペイロードが入力より長くなるため境界値でこの前提が崩れる
- 受信側 `H2Session::process_capsules` は Length を解釈した直後に `payload_len > config_.wt_max_capsule_payload_size` を検査し、`report_wt_error` で WT_ERROR (セッションエラー) としてセッションを閉じる
- 再現条件: 受信側の初期ストリームクレジット (`H2SessionConfig::wt_initial_max_stream_data`) が 1 MiB 以上の場合。既定は 262144 のため部分送出になり到達しないが、設定変更で到達する。`tests/test_webtransport_h2_input_limit.py` の `test_send_stream_data_accepts_one_mib` は `want_write()` までしか表明しておらず、ワイヤ到達時の挙動を検証していない
- 0158 (closed) は「自実装の送信は呼び出しごとに 1 カプセル化するため、カプセル単位の上限は正当な大容量転送を壊さない」と記録しているが、境界値では破綻する
- 影響: 自実装同士でも受信側の設定次第で 1 MiB の一括送信がセッション断になる

## 設計方針

- 送信側で capsule のペイロード長上限を守る。`H2Session::send_stream_data` と `H2Session::flush_pending_sends` は、ペイロード長が `wt_max_capsule_payload_size` を超えない範囲でデータを分割し、超過分は既存の保留キューへ回す (フロー制御の部分送出と同じ経路に載せる)
- 上限値の定義を 1 箇所に寄せる。Stream ID の varint 最大長を考慮した「1 capsule に入れられるデータ長」をヘルパで求め、`bindings::kMaxPythonInputBytes` との `static_assert` は「入力上限がペイロード上限以下」の形に直す。層ごとの入力上限を下げる案は採らない (0228 / 0232 が入力上限を層間で揃えている設計に反する)
- 部分送出時の FIN の扱いは既存の `flush_pending_sends` の規約 (部分送出では FIN を付けず、最後の断片にのみ付ける) を維持する
- 変更対象: `src/bindings/webtransport_h2.cpp`、`src/bindings/python_input.h`、`tests/test_webtransport_h2_input_limit.py`、`skills/webtransport-py/SKILL.md` (該当記述があれば)

## 完了条件

- 受信側の初期ストリームクレジットを 1 MiB 以上に設定した接続で、1 MiB ちょうどのデータを 1 回の `send_stream_data` で送っても、受信側が WT_ERROR でセッションを閉じない
- 分割された WT_STREAM capsule をすべて受信すると、送信したバイト列が欠落・重複なく復元できる
- `kMaxPythonInputBytes` の境界 (1 MiB ちょうどは通り、1 MiB + 1 は `ValueError`) は変わらない
- モックなしの実通信で検証できる (webtransport-py のテスト方針に従う)
- 全テストが通過する

## 解決方法
