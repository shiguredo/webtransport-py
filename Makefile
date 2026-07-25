.PHONY: wheel develop clean test lint format

# リリース用。PEP 517 isolation ありで再現性を優先する
wheel:
	uv build --wheel

# 開発用。venv 上の nanobind を使い、_build の増分ビルドを効かせる
# --inexact: package = false でも editable インストールを prune しない
develop:
	uv sync --inexact
	uv pip install --no-build-isolation -e .
	cp _build/*.so _build/py.typed src/webtransport/
	cp _build/webtransport_ext.pyi _build/__init__.pyi _build/h3.pyi _build/h2.pyi src/webtransport/
	cp _build/quic.pyi src/webtransport/quic/__init__.pyi
	cp _build/http2.pyi src/webtransport/http2/__init__.pyi
	cp _build/http3.pyi src/webtransport/http3/__init__.pyi

test:
	uv run pytest tests/ -v --timeout=30

lint:
	uv run ruff check src/ tests/ examples/

format:
	clang-format -i src/*.cpp src/bindings/*.cpp src/bindings/*.h
	uv run ruff format src/ tests/ examples/
