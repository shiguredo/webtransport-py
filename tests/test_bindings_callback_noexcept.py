"""C コールバックの `noexcept` 付与漏れを機械的に検出するテスト

CODEBASE.md の「C コールバック境界の例外方針」は、ngtcp2 / nghttp3 / nghttp2 /
BoringSSL へ登録する C コールバックに `noexcept` を付与することを定めている。
本テストは `src/` 配下の C++ ソースを走査し、次を検証する。

1. `*_cb` / `*_callback` の定義 (宣言と定義のすべての出現) に `noexcept` があること
2. C のコールバックとして登録する無名ラムダに `noexcept` があること
3. C のコールバックとして登録する関数が命名規則に従い、かつ 1 の走査で
   検査されていること

走査の前提は次のとおり。

- ブロックコメントと行コメント、文字列リテラル・文字リテラルの中は対象外とする
- 複数行に折り返した宣言・登録は 1 文に連結して判定する
- 手書きの `*_cb` / `*_callback` 定義を対象とする (マクロのトークン連結で名前を
  組み立てる定義と `#if 0` で無効化した定義は対象外)
- 接尾辞を持たない関数ポインタ枠 (`ngtcp2_settings::log_printf` など) は命名規則の
  外にあるため対象外
- nanobind の `.def` に渡すラムダと標準ライブラリへ渡すラムダは C のコールバック
  ではないため対象外 (前者は Python へ例外を伝播させる必要があるため `noexcept`
  を付けてはならない)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC_DIR = _REPO_ROOT / "src"
_SOURCE_PATTERNS = ("*.cpp", "*.cc", "*.cxx", "*.h", "*.hpp", "*.ipp")

# コールバックの命名規則 (CODEBASE.md の C コールバック境界の例外方針)
_CALLBACK_SUFFIXES = ("cb", "callback")
_SUFFIX_ALTERNATION = "|".join(_CALLBACK_SUFFIXES)
_CALLBACK_NAME = re.compile(rf"\b(\w+_(?:{_SUFFIX_ALTERNATION}))\s*\(")

# 定義とみなさない制御構文
_NOT_A_TYPE = ("return", "if", "else", "while", "for", "switch")

# コールバック構造体のフィールド (右辺は任意の識別子。命名規則の検査は test3 で行う)
_CALLBACK_FIELD_ASSIGNMENT = re.compile(
    r"^\s*(?:callbacks\.\w+|dr\.read_data|data_prd\.read_callback|"
    r"conn_ref_\.get_conn)\s*=\s*&?(?:\w+::)*([A-Za-z_]\w*)\s*;"
)
# nghttp2 のコールバックセッター (`..._set_xxx_callback(任意, xxx_cb)`)
_CALLBACK_SETTER = re.compile(r"nghttp2_session_callbacks_set_\w+\(\s*\w+\s*,\s*(\w+)\s*\)")
# 依存ライブラリの C API 呼び出し (引数で関数ポインタやラムダを渡す)
_C_REGISTRATION_CALL = re.compile(r"\b(?:SSL|ngtcp2|nghttp2|nghttp3)_\w+\s*\(")
# 依存ライブラリが提供するコールバック (自前の定義を持たず、登録するだけ)
_DEPENDENCY_PREFIXES = ("ngtcp2_crypto_", "nghttp2_", "nghttp3_")
# nanobind と標準ライブラリのラムダ (C のコールバックではない)
_NANOBIND_CALL = re.compile(r"nb::\w+|\.def\w*\(")
_STD_CALL = re.compile(r"\bstd::\w+\s*\(")

# 登録箇所を持たない自前のコールバック (他のコールバックから委譲して呼ばれる)
_DELEGATING_CALLBACKS = frozenset({"read_data_callback"})


class _Definition(NamedTuple):
    """コールバックの定義・宣言の出現"""

    name: str
    path: Path
    line: int
    signature: str


def _iter_sources() -> list[Path]:
    """`src/` 配下の C++ ソースとヘッダーを列挙する"""
    sources: list[Path] = []
    for pattern in _SOURCE_PATTERNS:
        sources.extend(_SRC_DIR.rglob(pattern))
    sources = sorted(set(sources))
    assert len(sources) >= 10, f"C++ ソースが少なすぎます: {len(sources)}"
    return sources


def _code_only(line: str) -> str:
    """行コメント・ブロックコメント・文字列リテラル・文字リテラルを除いたコードを返す

    ブロックコメントの開始は返り値に含めない (呼び出し側が状態を引き継ぐ)。
    """
    code = ""
    index = 0
    quote = ""
    while index < len(line):
        char = line[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "\"'":
            quote = char
            index += 1
            continue
        if line.startswith("//", index):
            break
        code += char
        index += 1
    return code


def _logical_lines(path: Path) -> list[tuple[int, str]]:
    """コメントとリテラルを除き、折り返した文を 1 行に連結して返す

    @return (開始行番号, 連結したコード) のリスト
    """
    logical: list[tuple[int, str]] = []
    in_block_comment = False
    pending = ""
    pending_line = 0
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = ""
        index = 0
        while index < len(raw):
            if in_block_comment:
                end = raw.find("*/", index)
                if end < 0:
                    index = len(raw)
                    continue
                in_block_comment = False
                index = end + 2
                continue
            start = raw.find("/*", index)
            if start < 0:
                code += _code_only(raw[index:])
                index = len(raw)
                continue
            code += _code_only(raw[index:start])
            in_block_comment = True
            index = start + 2

        code = code.strip()
        if not code:
            continue
        if pending:
            pending = f"{pending} {code}"
        else:
            pending = code
            pending_line = line_no
        if _is_complete_statement(pending):
            logical.append((pending_line, pending))
            pending = ""
    if pending:
        # 文が閉じないままファイル末尾に達した場合は走査が壊れている
        assert _is_complete_statement(pending), (
            f"文を閉じられませんでした (走査の前提が崩れています): "
            f"{path.relative_to(_REPO_ROOT)}:{pending_line}"
        )
        logical.append((pending_line, pending))
    return logical


def _is_complete_statement(text: str) -> bool:
    """丸括弧・角括弧の対応が取れ、継続演算子で終わっていない文を完結とみなす

    波括弧は数えない (関数本体を 1 文として連結しないため)。
    """
    depth = 0
    for char in text:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
    if depth > 0:
        return False
    return not text.endswith(("=", ",", "&&", "||", "+", "-", "*", "/", "->", "::"))


def _is_definition(text: str, name_start: int) -> bool:
    """コールバック名の手前に戻り値の型がある文を定義とみなす

    呼び出し (行頭が関数名・制御構文の直後・文字列リテラル内) を除外する。
    """
    prefix = text[:name_start].strip()
    if not prefix or prefix.endswith((".", ">")) or "=" in prefix:
        return False
    last = prefix.split()[-1]
    if last in _NOT_A_TYPE or "(" in last or ")" in last:
        return False
    return not prefix.endswith("(")


def _signature(text: str, start: int) -> str:
    """名前置から本体の開始 (`{`) または文末 (`;`) までを返す

    波括弧の入れ子 (既定引数の `{}` など) は本体の開始とみなさない。
    """
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            if depth == 0:
                return text[start:index]
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == ";":
            return text[start:index]
    return text[start:]


def _format_location(path: Path, line: int) -> str:
    """失敗メッセージ用の位置表記を返す"""
    return f"{path.relative_to(_REPO_ROOT)}:{line}"


def _scan_callback_definitions() -> list[_Definition]:
    """`src/` のコールバック定義・宣言をすべて走査する"""
    definitions: list[_Definition] = []
    for path in _iter_sources():
        for line_no, text in _logical_lines(path):
            for match in _CALLBACK_NAME.finditer(text):
                if not _is_definition(text, match.start()):
                    continue
                definitions.append(
                    _Definition(match.group(1), path, line_no, _signature(text, match.start()))
                )
    return definitions


def _scan_registered_callback_names() -> dict[str, str]:
    """コールバックとして登録している関数名を登録箇所から集める

    コールバック構造体のフィールドへの代入と、nghttp2 のコールバックセッター、
    依存ライブラリの C API 呼び出しの引数を対象にする。

    @return 関数名 → 登録箇所 (ファイル:行番号)
    """
    registered: dict[str, str] = {}
    for path in _iter_sources():
        for line_no, text in _logical_lines(path):
            assignment = _CALLBACK_FIELD_ASSIGNMENT.match(text)
            setter = _CALLBACK_SETTER.search(text)
            if assignment is not None:
                names = [assignment.group(1)]
            elif setter is not None:
                names = [setter.group(1)]
            else:
                # C API 呼び出しの引数に現れる関数ポインタ (名前付き関数)。
                # 呼び出し先の名前 (`SSL_CTX_sess_set_new_cb` 等) は含めない
                names = [
                    name
                    for call in _C_REGISTRATION_CALL.finditer(text)
                    for name in re.findall(r"\b(\w+)\b", _call_arguments(text, call.end()))
                    if name.endswith(_CALLBACK_SUFFIXES)
                ]
            for name in names:
                registered.setdefault(name, _format_location(path, line_no))
    return registered


def _call_arguments(text: str, start: int) -> str:
    """呼び出しの開き括弧の直後から対応する閉じ括弧までを返す

    @param text 対象の文
    @param start 開き括弧の直後の位置
    @return 引数の文字列 (閉じ括弧が無ければ文末まで)
    """
    depth = 1
    index = start
    while index < len(text):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[start:index]
        index += 1
    return text[start:]


def _missing_noexcept(definitions: list[_Definition]) -> list[str]:
    """`noexcept` が付いていない出現を位置つきで返す"""
    return [
        f"{_format_location(definition.path, definition.line)} {definition.name}"
        for definition in definitions
        if "noexcept" not in definition.signature
    ]


def test_bindings_callback_definitions_have_noexcept() -> None:
    """`*_cb` / `*_callback` の定義と宣言のすべてに `noexcept` があることを確認する

    関数スコープの静的コールバック (例: `wt_data_read_callback`)、クラスの
    メンバーとして定義されたコールバック (例: `Http3Connection::stream_close_cb`)、
    ヘッダーでの宣言のすべての出現を対象にする。
    """
    definitions = _scan_callback_definitions()
    assert len(definitions) >= 100, f"コールバックの出現が少なすぎます: {len(definitions)}"

    missing = _missing_noexcept(definitions)
    assert missing == [], "noexcept が付いていないコールバック:\n" + "\n".join(missing)


def test_bindings_lambda_callbacks_have_noexcept() -> None:
    """C のコールバックとして登録する無名ラムダの `noexcept` を確認する

    依存ライブラリの C API 呼び出しの引数、またはコールバック構造体のフィールド
    への代入で現れるラムダを対象にする。nanobind と標準ライブラリのラムダは
    対象外であり、前者には `noexcept` を付けてはならない (付いていれば失敗)。
    どちらにも分類できないラムダがあれば、走査の前提が崩れているため失敗させる。
    """
    missing = []
    unexpected = []
    unclassified = []
    c_checked = 0
    nanobind_checked = 0
    for path in _iter_sources():
        for line_no, text in _logical_lines(path):
            for match in re.finditer(r"\[\]\s*\(", text):
                prefix = text[: match.start()]
                signature = _signature(text, match.start())
                location = _format_location(path, line_no)
                if _C_REGISTRATION_CALL.search(prefix) or re.search(
                    r"[\w.>\-]+\.\w+\s*=\s*$", prefix
                ):
                    c_checked += 1
                    if "noexcept" not in signature:
                        missing.append(f"{location} (無名ラムダ)")
                elif _NANOBIND_CALL.search(prefix):
                    nanobind_checked += 1
                    if "noexcept" in signature:
                        unexpected.append(f"{location} (nanobind のラムダ)")
                elif _STD_CALL.search(prefix):
                    continue
                else:
                    unclassified.append(f"{location} {text[:60]}")

    assert unclassified == [], (
        "C のコールバックか nanobind のラムダか判定できない無名ラムダ:\n" + "\n".join(unclassified)
    )
    assert c_checked >= 1, "C のコールバックとして登録するラムダを 1 件も検出できていません"
    assert nanobind_checked >= 5, f"nanobind のラムダを検出できていません: {nanobind_checked}"
    assert missing == [], "noexcept が付いていない無名ラムダ:\n" + "\n".join(missing)
    assert unexpected == [], "nanobind のラムダに noexcept が付いている:\n" + "\n".join(unexpected)


def test_bindings_registered_callbacks_are_scanned() -> None:
    """登録箇所が指すコールバックが命名規則に従い、走査で検査されていることを確認する

    `*_cb` / `*_callback` の命名規則は走査の前提であるため、登録箇所から洗い出した
    関数が命名規則に従うことと、定義の走査結果に現れることを確認する。依存
    ライブラリが提供するコールバック (`ngtcp2_crypto_*` 等) は自前の定義を持たない
    ため対象から除く。
    """
    definitions = {definition.name for definition in _scan_callback_definitions()}
    registered = _scan_registered_callback_names()
    assert len(registered) >= 30, f"登録されたコールバックが少なすぎます: {len(registered)}"

    own = {
        name: location
        for name, location in registered.items()
        if not name.startswith(_DEPENDENCY_PREFIXES)
    }
    invalid = sorted(
        f"{location} {name}"
        for name, location in own.items()
        if not name.endswith(_CALLBACK_SUFFIXES)
    )
    assert invalid == [], (
        "C コールバックの命名規則 (*_cb / *_callback) に従っていない登録:\n" + "\n".join(invalid)
    )

    not_scanned = sorted(f"{own[name]} {name}" for name in own if name not in definitions)
    assert not_scanned == [], (
        "登録されているのに定義の走査で見つからないコールバック:\n" + "\n".join(not_scanned)
    )

    # 逆方向: 自前のコールバック定義は登録箇所から拾えていること
    # (登録行が消えても気付けるようにするための検査)
    unregistered = sorted(
        name
        for name in definitions
        if not name.startswith(_DEPENDENCY_PREFIXES)
        and name not in own
        and name not in _DELEGATING_CALLBACKS
    )
    assert unregistered == [], (
        "定義はあるのに登録箇所から拾えていないコールバック "
        "(登録されていないか、走査が登録箇所を取りこぼしている):\n" + "\n".join(unregistered)
    )
