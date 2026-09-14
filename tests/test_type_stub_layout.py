"""型スタブの配置が型検査から見て壊れていないことを検証するテスト

型スタブは「高レベル API を隠さない」配置でなければならない。PEP 561 の
解決順では、パッケージ直下の兄弟スタブ (`webtransport/http2.pyi`) と
パッケージを置き換える `webtransport/__init__.pyi` が、同名の実装
(`webtransport/http2/__init__.py` と `webtransport/__init__.py`) より優先
される。この状態になると、型検査からは `webtransport.http2.Client` や
`webtransport.WebTransportConnectError` が消え、examples の利用コードだけが
「モジュールにそんなメンバーは無い」と誤って報告される。

拡張モジュールのスタブは `webtransport/webtransport_ext/` 配下へ
スタブパッケージとして置き、高レベル API の型は実装 (`*.py`) 自身に
持たせる。ここではその不変条件を、型検査ツールを呼ばずに固定する。

- 実装が存在するモジュールを隠す `.pyi` が `src/webtransport/` 配下に無いこと
- 拡張モジュールのスタブパッケージが拡張モジュールの公開名と一致すること
- スタブパッケージの絶対 import が解決不能な形で残っていないこと
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import types
from pathlib import Path

# リポジトリ直下 (tests/ の親)
REPO_ROOT = Path(__file__).resolve().parent.parent
# スタブと実装が同居するソースツリー
SOURCE_DIR = REPO_ROOT / "src" / "webtransport"
# 拡張モジュール (nanobind) のスタブパッケージ
STUB_DIR = SOURCE_DIR / "webtransport_ext"
# 拡張モジュールが公開する低レベル API のモジュール名
MODULES = ("quic", "http3", "h3", "http2", "h2")
# スタブパッケージ自身がトップレベルに存在しないため、この絶対 import は解決できない
UNRESOLVABLE_PREFIX = "webtransport_ext"


def _public_submodules() -> set[str]:
    """拡張モジュールが実際に公開しているサブモジュール名を集める

    nanobind モジュールはサブモジュールを属性として生やすため、属性のうち
    モジュールであるものを公開サブモジュールとみなす。拡張モジュールが
    import できない環境 (wheel 未インストール等) では検証できないため、
    空集合を返して呼び出し側でスキップする。
    """
    if importlib.util.find_spec("webtransport.webtransport_ext") is None:
        return set()
    module = importlib.import_module("webtransport.webtransport_ext")
    return {
        name
        for name in dir(module)
        if not name.startswith("_") and isinstance(getattr(module, name), types.ModuleType)
    }


def test_no_stub_shadows_implementation() -> None:
    """実装を隠す兄弟スタブ・パッケージスタブが残っていないことを確認する

    `src/webtransport/` 直下と各モジュールディレクトリ直下の `.pyi` は、
    同名の実装を PEP 561 の解決順で隠す。スタブパッケージ
    (`webtransport_ext/`) の配下だけが `.pyi` を置いてよい場所である。
    """
    shadowing: list[str] = []
    for stub_path in sorted(SOURCE_DIR.rglob("*.pyi")):
        # スタブパッケージ配下は拡張モジュール専用の置き場なので対象外
        if STUB_DIR in stub_path.parents:
            continue
        shadowing.append(str(stub_path.relative_to(REPO_ROOT)))

    assert not shadowing, (
        "実装を隠す型スタブが残っている (高レベル API が型検査から消える):\n"
        + "\n".join(shadowing)
        + "\n`make develop` でスタブパッケージを作り直すこと"
    )


def test_stub_package_matches_extension_module() -> None:
    """スタブパッケージが拡張モジュールの公開名と一致することを確認する

    拡張モジュールへサブモジュールを追加したのにスタブを追加し忘れると、
    そのモジュールだけが型検査から見えなくなる。
    """
    public = _public_submodules()
    if not public:
        # 拡張モジュールが import できない環境では照合できない
        return

    missing = sorted(name for name in public if not (STUB_DIR / f"{name}.pyi").is_file())
    assert not missing, (
        "拡張モジュールのサブモジュールに対応するスタブが無い:\n"
        + "\n".join(missing)
        + "\n`make develop` でスタブを再生成すること"
    )

    # スタブパッケージ側の import が、実在するサブモジュールだけを指していること
    init_stub = STUB_DIR / "__init__.pyi"
    assert init_stub.is_file(), "スタブパッケージの __init__.pyi が無い"
    exported = {
        alias.name
        for node in ast.parse(init_stub.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    unexpected = sorted(exported - set(MODULES))
    assert not unexpected, (
        "スタブパッケージが拡張モジュールに無いサブモジュールを公開している:\n"
        + "\n".join(unexpected)
    )


def test_stub_package_has_no_unresolvable_import() -> None:
    """スタブパッケージに解決不能な絶対 import が残っていないことを確認する

    nanobind の stubgen はトップレベル `webtransport_ext` を import する形で
    出力するが、スタブパッケージ自身が `webtransport.webtransport_ext` に
    あるためこの import は解決できない。相対 import へ書き換えられている
    ことを確認する (`scripts/normalize_stubs.py` の後処理)。
    """
    problems: list[str] = []
    for stub_path in sorted(STUB_DIR.glob("*.pyi")):
        for node in ast.walk(ast.parse(stub_path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] == UNRESOLVABLE_PREFIX:
                        problems.append(f"{stub_path.name}: import {alias.name}")
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and (node.module or "").split(".")[0] == UNRESOLVABLE_PREFIX
            ):
                problems.append(f"{stub_path.name}: from {node.module} import ...")

    assert not problems, (
        "スタブパッケージに解決不能な絶対 import が残っている:\n"
        + "\n".join(problems)
        + "\n`uv run python scripts/normalize_stubs.py _build/webtransport_ext` を実行すること"
    )
