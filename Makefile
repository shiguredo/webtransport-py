.PHONY: wheel develop clean test

# リリース用。PEP 517 isolation ありで再現性を優先する
wheel:
	uv build --wheel

# 開発用。venv 上の nanobind を使い、_build の増分ビルドを効かせる
# --inexact: package = false でも editable インストールを prune しない
develop:
	uv sync --inexact
	uv pip install --no-build-isolation -e .
	cp _build/*.so _build/py.typed src/webtransport/
	# 旧レイアウトの型スタブ (兄弟 .pyi) は PEP 561 の解決順でサブパッケージの
	# __init__.py を隠すため、ビルドのたびに取り除く
	rm -f src/webtransport/*.pyi
	rm -f src/webtransport/quic/__init__.pyi
	rm -f src/webtransport/http2/__init__.pyi
	rm -f src/webtransport/http3/__init__.pyi
	rm -rf src/webtransport/webtransport_ext
	cp -R _build/webtransport_ext src/webtransport/webtransport_ext


test:
	uv run pytest tests/ -v --timeout=30
