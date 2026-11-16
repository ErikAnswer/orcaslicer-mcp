# The ErikAnswer fork

This repository is a fork of [MaxEllis/orcaslicer-mcp](https://github.com/MaxEllis/orcaslicer-mcp). It is
installed from its git tags (`vX.Y.Z-erik.N`) and publishes nowhere: not to PyPI, the MCP registry, OrcaCloud,
LobeHub, Glama or GitHub releases.

## What still names upstream, and why

The package on PyPI, the OrcaSlicer MCP plugin on OrcaCloud and the listings are MaxEllis's, so the text that
points at them is correct and stays as upstream wrote it:

- `README.md`, `pyproject.toml`, `.claude-plugin/`, `.github/FUNDING.yml`, `plugin/` and `docs/` (except this
  file, which exists only in the fork).
- `packaging/lhm.plugin.json` and `packaging/mcpb/manifest.json`. Nothing here publishes them; tests read both,
  and `scripts/gen_lhm_tools.py` keeps the `tools` list of the first in step with this fork's tools.
- The notices for the plugin on stock OrcaSlicer (`src/orcaslicer_mcp/plugin_update.py`, `plugin_text.py`,
  `errors.py`). The plugin is released only upstream, and these notices never appear with the fork build.

  `LATEST_PLUGIN_VERSION` follows the plugin file in this fork, so it can run ahead of what upstream has
  released (0.2.4 here while upstream and OrcaCloud still ship 0.2.3). Until upstream releases that version, a
  stock-OrcaSlicer user of this fork would be told about a plugin the link does not offer yet. This is accepted
  (#1023): nobody runs this fork with the stock plugin, and the gap closes on the next merge from upstream.

Rewriting any of these to name the fork would conflict with every upstream release and point at releases that
do not exist.

## What the fork removes

Everything that publishes: `scripts/cut-release.sh`, `scripts/cut-plugin-release.sh`, `.github/workflows/`,
`packaging/server.json`, `plugin/orcacloud/` and `glama.json`. A merge from upstream brings them back as
conflicts or new files; delete them again. `tests/test_fork_not_published.py` fails while any of them exists.
The test knows these paths by name: when a merge brings a publishing script or manifest under a new name, delete
it and add its path to the list in that test.
