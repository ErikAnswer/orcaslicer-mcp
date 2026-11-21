# Development

```bash
uv venv && uv pip install -e ".[dev]"
uv run pytest   # more than 1,200 tests against a mock API, plus a guarded live smoke test
```

The live smoke test skips itself unless `ORCA_API_URL` and `ORCA_API_TOKEN` point at a running OrcaSlicer MCP build. Protocol notes live in [`docs/`](.).

## Settings schema

`describe_setting`, `search_settings` and the `about` of `get_config` read
`src/orcaslicer_mcp/data/print_settings_schema.json`, built from `src/libslic3r/PrintConfig.cpp` of OrcaSlicer:
the official 2.4.2, 2.5.0 and 2.5.1 and our fork (ErikAnswer/OrcaSlicer `main`). A key the fork adds says
`"since": "fork"`; one 2.5 added says `"since": "2.5.0"`; one a newer line dropped says `removed_in`.

Regenerate it whenever the fork adds or changes a setting (a new `this->add(...)` in `PrintConfig.cpp`),
or when the slicer moves to a new OrcaSlicer version. From a checkout of the slicer with its tags (on the
devbox, `~/source/OrcaSlicer-mcp`, after `git fetch --tags origin` and with `main` up to date):

```bash
PC=~/source/OrcaSlicer-mcp/src/libslic3r/PrintConfig.cpp
for v in 2.4.2 2.5.0 2.5.1; do
  python scripts/extract_settings_schema.py --src $PC --ref v$v --date 2026-11-09 --out /tmp/schema-$v.json
done
python scripts/extract_settings_schema.py --src $PC --ref main --out /tmp/schema-fork.json
python scripts/merge_settings_schemas.py /tmp/schema-2.4.2.json 2.4.2 /tmp/schema-2.5.0.json 2.5.0 \
  /tmp/schema-2.5.1.json 2.5.1 /tmp/schema-fork.json fork
```

`--ref` reads the file as it is at that tag or commit, so the checkout can stay on any branch. The official
lines keep their extraction date so their part of the file does not change; on a new OrcaSlicer version,
add its tag as one more pair before `fork`. Check `git diff` of the JSON (only the keys you expect should
appear), add the new keys to `tests/test_settings_artifact.py` if they matter, then refresh the labels the plugin
embeds (`plugin/tests/test_embedded.py` fails until you do), keeping its panel as it is:

```bash
python3 -I -c "import ast; m = ast.parse(open('plugin/orcaslicer_mcp_plugin.py', encoding='utf-8').read()); \
  v = next(n.value for n in m.body if isinstance(n, ast.Assign) and getattr(n.targets[0], 'id', '') == 'PANEL_HTML'); \
  open('/tmp/panel.html', 'w', encoding='utf-8', newline='').write(ast.literal_eval(v))"
(cd plugin && python3 -I tools/embed.py /tmp/panel.html ../src/orcaslicer_mcp/data/print_settings_schema.json)
```

Release as usual.
