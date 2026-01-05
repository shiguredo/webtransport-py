.PHONY: wheel develop clean test lint format

wheel:
	uv build --wheel

develop: wheel
	uv pip install -e . --force-reinstall
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
