# 実質検証していないテストを修正する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/test-fix-ineffective-assertions
- Polished: 2026-09-15

## 目的

表明が恒真になっていたり、条件付きで実行されなかったりして、テストが検証していると称する性質を実際には見ていない箇所がある。回帰を検出できないテストは保守コストだけを生む。本 issue は既存の表明と invariant の実効性だけを扱う。

## 現状

条件付きの表明:

- `tests/prop_quic_handshake.py` のハンドシェイク後のストリームデータ送出を確認するテストが `if received_data: assert received_data == data` になっており、データが 1 バイトも届かなければ表明が実行されずに成功する。実測では 30 例すべてで `received_data` が空であり、表明は一度も実行されていない

恒真の表明:

- `tests/prop_http3.py` の `submit_request` と `tests/prop_webtransport_h3.py` の `open_stream` に `assert result is False or result is True` がある (bool に対して恒真)
- `tests/prop_webtransport_h2.py` の `connect` 系で `assert result >= -1` がある。SETTINGS 交換前の呼び出しのため `H2Session::connect` は常に -1 を返し、より強い表明が可能である
- `tests/prop_http2.py` に `isinstance` だけを見る表明がある
- `tests/test_quic_free_threading.py` のハンマー後の表明 `assert isinstance(client.is_closed(), bool)` が 5 テストにある。`is_closed()` は `-> bool` を宣言しているため恒真で、実際に検証しているのはワーカースレッドが例外を投げないことだけである

例外の包括捕捉:

- `tests/prop_http3.py` の 4 箇所 (`receive_stream_data` / `submit_request` / `bind_control_stream` / `bind_qpack_streams`) と `tests/prop_webtransport_h3.py` の 1 箇所 (`receive_stream_data`) に `except ValueError, TypeError, OverflowError: return` がある (Python 3.14 の PEP 758 構文)。入力の値域を問わず例外が出れば成功扱いになるため、有効な値まで例外にし始めた退行を検出できない

stateful PBT の invariant:

- `tests/prop_h2_stateful.py` はクラスの docstring で「クレジットが送信のたびに単調減少し、対向の WT_MAX_DATA 受信で回復することを invariant で観測する」と書いているが、invariant は `credit >= 0` と `credit <= h2.Config().wt_initial_max_data` のみを見ている。`last_credit` は代入だけで参照されていない
- 同 invariant の上限表明は固定値 (既定 1048576) との比較であり、クレジットの実体 (そのときに受信した広告値から送信済みバイト数を引いた値) を反映していない。現状は state machine の送信量が 1 MiB に届かないため通っているが、回復経路を踏ませると正当な状態で落ちる
- `tests/prop_quic_stateful.py` は docstring で「送受信バイト数が破綻しないことを見る」と書いているが、その invariant は存在しない。`sent_bytes` は代入だけで参照されていない
- `tests/prop_h3_stateful.py` の `received_datagrams` / `client_sent_bytes` も参照されていない

その他:

- `tests/prop_*.py` で `@given` を持たないトップレベル関数が 25 件ある。内訳は `tests/prop_http3.py` 8 件、`tests/prop_webtransport_h3.py` 11 件、`tests/prop_webtransport_h2.py` 5 件、`tests/prop_quic.py` 1 件で、関数名の接頭辞は `prop_` が 13 件、`test_` が 12 件と同一ファイル内で混在している。`pyproject.toml` の `python_functions = ["test_*", "prop_*"]` により 25 件すべてが pytest に収集される
- `tests/prop_http2_roundtrip.py` に、`@given` 付きテストから呼ばれるヘルパー関数が 5 つある (`create_client_server_pair` / `exchange_settings` / `valid_header_name` / `valid_header_value` / `exchange_data_after_request`)。接頭辞が無いため pytest には収集されないが、テストファイルのトップレベルに置かれている
- `tests/test_webtransport_h3_error_code_remap.py` の `parametrize` に `ids` が無い。同じファイルの hypothesis 版が同一プロパティを包含しており、代表値版は真部分集合である
- `tests/test_quic_pacing.py` の未来の期限を確認する表明が `assert 0 <= timeout < 5_000_000_000` で、下限が 0 のため「期限到来済み」も通る。ただし `_stall_timeout_with_room` は `timeout > 0` のときだけ返すため下限は到達せず、表明は実質「5 秒未満」だけを見ている。同じファイルの別テストの `0 < timeout < 1_000_000_000` とは上限が異なるが、5 秒の上限は flaky 対策として緩和された値である
- `tests/test_udp_resolution.py` の `assert _first_family() in (socket.AF_INET, socket.AF_INET6)` は実質恒真である

なお `issues/pending/0126-test-strengthen-tests.md` は「PBT の property 追加と flaky 解消」を扱い、property 追加と flaky 解消は完了済みで、残りは H3 のセッションフロー制御 (0092) の再開待ちのみである。重複しない。

## 設計方針

- 表明は「何が起きれば失敗すべきか」を書く。条件付きの表明は前提を満たすようにテストを組み立ててから無条件に表明する
- 恒真の表明は、期待する具体的な値または状態を表明する形に置き換える。置き換えられないならテストごと削除する
- 例外を許容するテストは、ストラテジを有効域と無効域に分け、無効域でのみ例外を許容する
- invariant は docstring が主張している性質を実際に観測する。観測しない状態変数は削除する
- h2 のクレジットは「送信 rule で減り、ポンプ rule で広告値を受けて増える」という性質である。docstring の「単調減少」と「回復」を両立させるため、増加を観測する基準の取り直しを解決方法で指定する
- 上限の表明は固定値ではなく、導出できる期待値または「窓を超えて送らない」性質にする
- `@given` を持たない関数は、PBT 化して `@given` を持たせる場合はそのまま置き、そうでなければテストなら `test_` へ改名して `test_*.py` へ移し、ヘルパーなら収集対象外の名前にするか `tests/conftest.py` へ移す

## 完了条件

- 条件付きの表明が無条件になり、データが届かなければ失敗する
- 列挙した恒真の表明が、失敗すべきときに失敗する表明になっている
- 例外の包括捕捉が、無効域でのみ例外を許容する形になっている
- stateful PBT の invariant が docstring の主張と一致し、未参照の状態変数が削除される。h2 のクレジットの invariant は固定値の上限比較を含まない
- `prop_*.py` で `@given` を持たないのに収集される関数が 0 件になる (12 件の `test_` は `test_*.py` へ移り、13 件の `prop_` は PBT 化するか `test_*.py` へ移る)。判定は AST で `prop_*.py` のトップレベル関数のうち `@given` を持たず `test_` / `prop_` で始まるものを列挙して 0 件であることを確認する
- 恒真の表明の置き換えは、対象の表明を意図的に破った状態で対応するテストが失敗することを確認している
- 「その他」の各項目が処理されている。`tests/test_webtransport_h3_error_code_remap.py` の代表値版テストと `tests/test_udp_resolution.py` の `test_localhost_first_family_noted` が削除され、`tests/test_quic_pacing.py` の下限が到達しない根拠がコメントで残り、`tests/prop_http2_roundtrip.py` の 5 つのヘルパー関数が収集対象外になっている
- 全テストが通過する

## 解決方法

- `tests/prop_quic_handshake.py` はサーバーへの `receive` でデータが届くことを前提に組み立て、`received_data` の非空を表明してから内容を表明する
- `tests/prop_http3.py` の `submit_request` と `tests/prop_webtransport_h3.py` の `open_stream` は、恒真の表明を期待する具体的な値 (bool なら `is True` / `is False` のどちらを期待するか) に置き換える
- `tests/prop_webtransport_h2.py` の `assert result >= -1` を `assert result == -1` に置き換える (SETTINGS 交換前のため `is_webtransport_ready` が false で常に -1)
- `tests/prop_http2.py` の `isinstance` だけの表明を、`submit_request` が返す具体的なストリーム ID の表明に置き換える
- `tests/test_quic_free_threading.py` の 5 テストの `is_closed()` の表明を、ハンマー後に期待する具体的な状態 (接続が閉じているかどうかとその理由) の表明に置き換える
- `tests/prop_http3.py` の 4 箇所と `tests/prop_webtransport_h3.py` の 1 箇所の包括捕捉を、ストラテジを有効域と無効域に分けたうえで無効域でのみ例外を許容する形に直す
- `tests/prop_h2_stateful.py` の invariant を次の形にする。`pump_server_to_client` の直後に基準を取り直し、それ以外の rule の前後でクレジットが増えていないことを表明する。上限は固定値ではなく、最後に受信した広告値から送信済みバイト数を引いた期待値、または「送信済みバイト数が広告値を超えない」性質で表明する。`last_credit` は基準の取り直しに使う
- `tests/prop_quic_stateful.py` の `sent_bytes` を使った invariant (送受信バイト数が破綻しない) を実装するか、docstring の主張を実際に観測する内容へ書き換えて `sent_bytes` を削除する
- `tests/prop_h3_stateful.py` の `received_datagrams` / `client_sent_bytes` を観測する invariant を実装するか、これらの変数を削除する
- `tests/test_webtransport_h3_error_code_remap.py` の代表値版テストは削除する (hypothesis 版と端点テストが同じ値を包含している)
- `tests/test_quic_pacing.py` の下限は到達しないため、その根拠をコメントに残す (上限は flaky 対策の 5 秒を維持する)
- `tests/test_udp_resolution.py` の `test_localhost_first_family_noted` は削除する (記録の役割は `_first_family()` を使う `test_localhost_fallback_exercised` が担う)
- `tests/prop_http2_roundtrip.py` の 5 つのヘルパー関数を `tests/conftest.py` へ移す (`_` 前置にすると収集対象外になるが、複数のテストファイルから使う想定なら conftest が適切)
- `@given` を持たない `prop_*` のテスト (13 件) は PBT 化するか `test_*.py` へ改名して移す
- `tests/test_quic_free_threading.py` は別 issue (0228) も変更するため、競合した場合はリベースする
