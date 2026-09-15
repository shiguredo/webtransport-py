# 実質検証していないテストを修正する

- Created: 2026-09-15
- Completed: {YYYY-MM-DD}
- Branch: feature/test-fix-ineffective-assertions
- Polished: {YYYY-MM-DD}

## 目的

表明が恒真になっていたり、条件付きで実行されなかったりして、テストが検証していると称する性質を実際には見ていない箇所がある。回帰を検出できないテストは保守コストだけを生む。

## 現状

条件付きの表明:

- `tests/prop_quic_handshake.py` のハンドシェイク後のストリームデータ送出を確認するテストが `if received_data: assert received_data == data` になっており、データが 1 バイトも届かなければ表明が実行されずに成功する

恒真の表明:

- `tests/prop_http3.py` と `tests/prop_webtransport_h3.py` の `submit_request` 系で `assert result is False or result is True` がある (bool に対して恒真)
- `tests/prop_webtransport_h2.py` の `connect` 系で `assert result >= -1` がある
- `tests/prop_http2.py` に `isinstance` だけを見る表明がある
- `tests/test_quic_free_threading.py` のハンマー後の表明が `assert isinstance(client.is_closed(), bool)` で、実際に検証しているのはワーカースレッドが例外を投げないことだけである

例外の包括捕捉:

- `tests/prop_http3.py` と `tests/prop_webtransport_h3.py` に `except ValueError, TypeError, OverflowError: return` があり、入力の値域を問わず例外が出れば成功扱いになる。有効な値まで例外にし始めた退行を検出できない

stateful PBT の invariant:

- `tests/prop_h2_stateful.py` はクラスの docstring で「クレジットが送信のたびに単調減少し、対向の WT_MAX_DATA 受信で回復することを invariant で観測する」と書いているが、invariant は `credit >= 0` と `credit <= h2.Config().wt_initial_max_data` のみを見ている。`last_credit` は代入だけで参照されていない
- `tests/prop_quic_stateful.py` は docstring で「送受信バイト数が破綻しないことを見る」と書いているが、その invariant は存在しない。`sent_bytes` は代入だけで参照されていない
- `tests/prop_h3_stateful.py` の `received_datagrams` / `client_sent_bytes` も参照されていない

その他:

- `tests/prop_*.py` に `@given` を持たず 1 例だけ実行するテストが 25 件ある。ファイル名の接頭辞が `prop_` と `test_` で混在している
- `tests/test_webtransport_h3_error_code_remap.py` の `parametrize` に `ids` が無い。同じファイルの hypothesis 版が同一プロパティを包含しており、代表値版は従属している
- `tests/test_quic_pacing.py` の未来の期限を確認する表明が `assert 0 <= timeout < 5_000_000_000` で、下限が 0 のため「期限到来済み」も通る。同じファイルの別テストは `0 < timeout` を使っており不統一である
- `tests/test_udp_resolution.py` の `assert _first_family() in (socket.AF_INET, socket.AF_INET6)` は実質恒真である

なお `issues/pending/0126-test-strengthen-tests.md` は「PBT の網羅強化と flaky 解消」を扱っており、property の追加と flaky の修正は同 issue が担当する。本 issue は既存の表明と invariant の実効性だけを扱う。

## 設計方針

- 表明は「何が起きれば失敗すべきか」を書く。条件付きの表明は前提を満たすようにテストを組み立ててから無条件に表明する
- 恒真の表明は、期待する具体的な値または状態を表明する形に置き換える。置き換えられないなら表明ごと削除する
- 例外を許容するテストは、ストラテジを有効域と無効域に分け、無効域でのみ例外を許容する
- invariant は docstring が主張している性質を実際に観測する。観測しない状態変数は削除する
- `@given` を持たないテストは `prop_` の接頭辞を外して `test_` にし、PBT で書けるものは PBT に寄せる

## 完了条件

- 上記の恒真・条件付きの表明が、失敗すべきときに失敗する表明になっている
- stateful PBT の invariant が docstring の主張と一致し、未参照の状態変数が削除される
- `prop_*.py` に `@given` を持たないテストが残っていない (PBT に寄せたか `test_` に改名して移した状態)
- 全テストが通過する

## 解決方法

- `tests/prop_quic_handshake.py` のポンプを回してから無条件に表明する形へ直す
- `tests/prop_http3.py` / `prop_webtransport_h3.py` / `prop_webtransport_h2.py` / `prop_http2.py` の恒真の表明と包括捕捉を修正する
- `tests/prop_h2_stateful.py` / `prop_quic_stateful.py` / `prop_h3_stateful.py` の invariant を docstring の主張に合わせ、未参照の状態変数を削除する
- `tests/test_quic_free_threading.py` のハンマー後の表明を、期待する状態を確認する形に置き換える
- `tests/test_webtransport_h3_error_code_remap.py` の代表値版テストを削除するか、`ids` を付けて検証対象を表す名前に変える
- `tests/test_quic_pacing.py` の下限を `0 <` に変える
- `@given` を持たない `prop_*` テストを PBT 化するか `test_*` へ改名して適切なファイルへ移す
