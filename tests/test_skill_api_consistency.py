"""SKILL.md に載っている API が実装に存在するかを機械的に検証するテスト

`skills/webtransport-py/SKILL.md` は利用者と LLM の双方が最初に参照する
仕様書だが、実装が変わっても SKILL 側は誰も検証しないため、放置すると
存在しない API を案内し続ける。人手のレビューだけでは同じ乖離が再発する
ため、SKILL に書かれた API 名を実装と突き合わせて機械的に検出する。

対象は次の 2 種類。

- `<module>.<Class>.<member>` 形式の dotted 名
- フェンス付き python ブロックに並ぶシグネチャ一覧のメソッド名・プロパティ名

判定は「SKILL に載っている名前が実装に無い」場合だけを失敗にする。実装に
あって SKILL に無い API は対象外にする (SKILL は網羅を目的とした一覧では
ないため)。引数名・既定値・戻り値までは見ない (SKILL の表現揺れを許容し、
メンテナンスコストを上げないため)。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

# リポジトリ直下 (tests/ の親)
REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / "skills" / "webtransport-py" / "SKILL.md"
STUB_DIR = REPO_ROOT / "src" / "webtransport" / "webtransport_ext"
SOURCE_DIR = REPO_ROOT / "src" / "webtransport"

# SKILL が扱うモジュール。Sans I/O は webtransport_ext の stub、asyncio
# ラッパーは src/webtransport/<module>/ の実装から集める
MODULES = ("quic", "http3", "h3", "http2", "h2")

# フェンス付き python ブロック
PYTHON_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.DOTALL)
# `mod.Class` / `mod.func` 形式の参照
DOTTED_RE = re.compile(r"\b(" + "|".join(MODULES) + r")\.([A-Za-z_]\w*)(?:\.([A-Za-z_]\w*))?")
# 散文が示す所有者 (`mod.Class`)
OWNER_RE = re.compile(r"`((?:" + "|".join(MODULES) + r")\.[A-Za-z_]\w*)")
# 散文が示す所有者 (モジュール修飾なしのクラス名)
BARE_OWNER_RE = re.compile(r"`([A-Z][A-Za-z_]\w*)`")
# ブロック内で所有者を上書きするコメント (`# Class のメソッド`)
INLINE_OWNER_RE = re.compile(r"#\s*([A-Za-z_]\w*) のメソッド")
# シグネチャ一覧の各行
DEF_RE = re.compile(r"^(?:async )?def ([A-Za-z_]\w*)\s*\(")
PROPERTY_RE = re.compile(r"^([A-Za-z_]\w*) -> ")
STATIC_RE = re.compile(r"^([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\(")


def _collect_members(tree: ast.Module, result: dict[str, set[str]]) -> None:
    """クラス名 → メンバー名、モジュール直下の関数名を集める"""
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            members = result.setdefault(node.name, set())
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    members.add(item.name)
                elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    # プロパティ (スタブは @property + def、実装は変数注釈)
                    members.add(item.target.id)
                elif isinstance(item, ast.Assign):
                    # Enum のメンバー
                    for target in item.targets:
                        if isinstance(target, ast.Name):
                            members.add(target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            result.setdefault("", set()).add(node.name)


def _load_api() -> dict[str, dict[str, set[str]]]:
    """モジュールごとの「クラス名 → メンバー名」表を作る"""
    api: dict[str, dict[str, set[str]]] = {}
    for module in MODULES:
        table: dict[str, set[str]] = {}
        _collect_members(
            ast.parse((STUB_DIR / f"{module}.pyi").read_text(encoding="utf-8")),
            table,
        )
        for path in sorted((SOURCE_DIR / module).glob("*.py")):
            _collect_members(ast.parse(path.read_text(encoding="utf-8")), table)
        api[module] = table
    return api


def _resolve_bare_class(api: dict[str, dict[str, set[str]]], name: str) -> str | None:
    """モジュール修飾なしのクラス名を一意に解決する

    複数のモジュールに同名クラスがある場合は解決しない (誤判定を避ける)。
    """
    found = [f"{module}.{name}" for module in MODULES if name in api[module]]
    if len(found) != 1:
        return None
    return found[0]


def _skill_lines() -> list[str]:
    return SKILL_PATH.read_text(encoding="utf-8").splitlines()


@pytest.fixture(scope="module")
def api() -> dict[str, dict[str, set[str]]]:
    """モジュールごとの API 表"""
    return _load_api()


def test_dotted_api_names_exist(api: dict[str, dict[str, set[str]]]) -> None:
    """`<module>.<Class>.<member>` 形式の参照が実装に存在することを確認する"""
    problems: list[str] = []
    for line_no, line in enumerate(_skill_lines(), 1):
        for match in DOTTED_RE.finditer(line):
            module, first, second = match.group(1), match.group(2), match.group(3)
            table = api[module]
            if first not in table:
                problems.append(f"{line_no} 行目: {module}.{first} が実装に無い")
            elif second is not None and second not in table[first]:
                problems.append(f"{line_no} 行目: {module}.{first}.{second} が実装に無い")
    assert not problems, "SKILL.md に実装が無い API が載っている:\n" + "\n".join(problems)


def _signature_names(body: str, owner_class: str) -> list[str] | None:
    """シグネチャ一覧ブロックからメンバー名を取り出す

    シグネチャ行以外 (実行文など) を含むブロックは対象外として None を返す。

    Args:
        body: フェンス付きブロックの中身
        owner_class: ブロックが並べているクラス名 (モジュール修飾なし)

    Returns:
        メンバー名のリスト。シグネチャ一覧でない場合は None
    """
    names: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = DEF_RE.match(line)
        if match:
            names.append(match.group(1))
            continue
        match = PROPERTY_RE.match(line)
        if match:
            names.append(match.group(1))
            continue
        match = STATIC_RE.match(line)
        if match and match.group(1) == owner_class:
            names.append(match.group(2))
            continue
        return None
    return names


def _find_owner(
    api: dict[str, dict[str, set[str]]],
    lines: list[str],
    fence_line: int,
    body: str,
) -> str | None:
    """シグネチャ一覧ブロックの所有者クラスを解決する

    ブロック内の `# <Class> のメソッド` コメントを最優先し、無ければ直前の
    散文にある `mod.Class` を使う。モジュール修飾が無い場合はクラス名が
    一意なときだけ解決する。

    Args:
        api: モジュールごとの API 表
        lines: SKILL.md の行
        fence_line: ブロック開始行 (0 始まり)
        body: ブロックの中身

    Returns:
        `module.Class` 形式の所有者。解決できない場合は None
    """
    inline = INLINE_OWNER_RE.findall(body)
    if inline:
        return _resolve_bare_class(api, inline[-1])

    # 直前の散文と見出し (空行 3 つまで遡る) から所有者の候補を近い順に集める
    candidates: list[str] = []
    blanks = 0
    for index in range(fence_line - 1, max(fence_line - 25, -1), -1):
        line = lines[index]
        if not line.strip():
            blanks += 1
            if blanks > 2:
                break
            continue
        candidates.extend(OWNER_RE.findall(line))
        for bare in BARE_OWNER_RE.findall(line):
            resolved = _resolve_bare_class(api, bare)
            if resolved is not None:
                candidates.append(resolved)
    if not candidates:
        return None

    # ブロックが `Class.method(...)` 形式を含む場合はそのクラスを優先する。
    # 散文が別クラス (結線先など) に言及していても、列挙しているクラスが正
    for raw in body.splitlines():
        match = STATIC_RE.match(raw.strip())
        if match:
            for candidate in candidates:
                if candidate.split(".", 1)[1] == match.group(1):
                    return candidate
            break
    return candidates[0]


def test_signature_block_members_exist(api: dict[str, dict[str, set[str]]]) -> None:
    """シグネチャ一覧ブロックのメンバーが実装に存在することを確認する"""
    text = SKILL_PATH.read_text(encoding="utf-8")
    lines = _skill_lines()
    problems: list[str] = []
    checked = 0
    for match in PYTHON_BLOCK_RE.finditer(text):
        fence_line = text[: match.start()].count("\n")
        owner = _find_owner(api, lines, fence_line, match.group(1))
        if owner is None:
            continue
        module, class_name = owner.split(".", 1)
        table = api[module]
        if class_name not in table:
            problems.append(f"{fence_line + 1} 行目: {owner} が実装に無い")
            continue
        names = _signature_names(match.group(1), class_name)
        if names is None:
            continue
        checked += 1
        for name in names:
            if name == "__init__":
                continue
            if name not in table[class_name]:
                problems.append(f"{fence_line + 1} 行目: {owner}.{name} が実装に無い")
    # 抽出そのものが壊れると検証が空振りするため、対象ブロック数を確認する
    assert checked >= 10, f"シグネチャ一覧ブロックの抽出数が少なすぎる: {checked}"
    assert not problems, "SKILL.md のシグネチャ一覧に実装が無い API がある:\n" + "\n".join(problems)
