# The ErikAnswer fork

This repository is a fork of [MaxEllis/orcaslicer-mcp](https://github.com/MaxEllis/orcaslicer-mcp). It is
installed from its git tags (`vX.Y.Z-erik.N`) and publishes nowhere: not to PyPI, the MCP registry, OrcaCloud,
LobeHub, Glama or GitHub releases.

## What still names upstream, and why

The package on PyPI, the OrcaSlicer MCP plugin on OrcaCloud and the listings are MaxEllis's, so the text that
points at them is correct and stays as upstream wrote it:

- `README.md`, `pyproject.toml`, `.claude-plugin/`, `plugin/` and `docs/`.
- `packaging/lhm.plugin.json` and `packaging/mcpb/manifest.json`. Nothing here publishes them; tests read both,
  and `scripts/gen_lhm_tools.py` keeps the `tools` list of the first in step with this fork's tools.
- The notices for the plugin on stock OrcaSlicer (`src/orcaslicer_mcp/plugin_update.py`, `plugin_text.py`,
  `errors.py`). The plugin is released only upstream, and these notices never appear with the fork build.

Rewriting any of these to name the fork would conflict with every upstream release and point at releases that
do not exist.

## What the fork removes

Everything that publishes: `scripts/cut-release.sh`, `scripts/cut-plugin-release.sh`, `.github/workflows/`,
`packaging/server.json`, `plugin/orcacloud/` and `glama.json`. A merge from upstream brings them back as
conflicts or new files; delete them again. `tests/test_fork_not_published.py` fails while any of them exists.
