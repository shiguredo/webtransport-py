"""nanobind stubgen の出力を追跡できる形に整える

stubgen の出力はそのままでは次の点で使えない。

- `from webtransport_ext import ...` という解決不能な絶対 import を含む
  (スタブパッケージ自身が `webtransport.webtransport_ext` にあるため
  `webtransport_ext` という最上位モジュールは存在しない)
- import がソートされておらず ruff の I001 に違反する
- 整形が ruff format の結果と一致しない

生成物をスタブパッケージとして追跡するため、相対 import への書き換えと
ruff による整形・ソートをこの順で行う。ディレクトリを渡すと ruff が
.pyi を対象にしないため、対象ファイルを明示して渡す。

Usage:
    uv run python scripts/normalize_stubs.py <stub-dir>
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: normalize_stubs.py <stub-dir>", file=sys.stderr)
        return 2

    stub_dir = Path(sys.argv[1])
    if not stub_dir.is_dir():
        print(f"not a directory: {stub_dir}", file=sys.stderr)
        return 2

    paths = sorted(str(path) for path in stub_dir.glob("*.pyi"))
    if not paths:
        print(f"no .pyi found in {stub_dir}", file=sys.stderr)
        return 2

    for path in paths:
        text = Path(path).read_text(encoding="utf-8")
        text = text.replace("from webtransport_ext import ", "from . import ")
        text = text.replace("import webtransport_ext as ", "from . import ")
        Path(path).write_text(text, encoding="utf-8")

    subprocess.run(["uv", "run", "ruff", "format", *paths], check=True)
    subprocess.run(
        ["uv", "run", "ruff", "check", "--fix", "--select", "I001", *paths],
        check=True,
    )
    subprocess.run(["uv", "run", "ruff", "format", *paths], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
