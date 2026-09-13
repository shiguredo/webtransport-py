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
	# 追跡しているスタブと一致させるため、生成物に後処理をかけてからコピーする。
	# stubgen の出力は import が非ソートで、`from webtransport_ext import ...`
	# という解決不能な絶対 import を含むため、ruff と相対 import への書き換えで
	# 整える
	uv run python scripts/normalize_stubs.py _build/webtransport_ext
	cp -R _build/webtransport_ext src/webtransport/webtransport_ext
	# 追跡しているスタブが生成結果と一致することを確認する (乖離していれば
	# ここで失敗するので、stub の更新漏れに気付ける)
	diff -r _build/webtransport_ext src/webtransport/webtransport_ext


test:
	uv run pytest tests/ -v --timeout=30
