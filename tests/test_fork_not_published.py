"""This fork is installed from its git tags and publishes nowhere (see docs/fork.md).

A merge from upstream brings its publishing scripts and workflows back; with them in the tree, a tag pushed
here would try to publish under the upstream identity. Delete them again in the merge."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORK_WORKFLOWS = {"tests.yml"}
UPSTREAM_PUBLISHING = [
    "scripts/cut-release.sh",
    "scripts/cut-plugin-release.sh",
    "packaging/server.json",
    "plugin/orcacloud",
    "glama.json",
]


@pytest.mark.parametrize("path", UPSTREAM_PUBLISHING)
def test_upstream_publishing_stays_out_of_the_fork(path):
    assert not (ROOT / path).exists(), f"{path} publishes under the upstream identity: remove it (docs/fork.md)"


def test_only_the_fork_workflows_are_in_the_tree():
    found = {path.name for path in (ROOT / ".github" / "workflows").glob("*")}
    assert found <= FORK_WORKFLOWS, f"{sorted(found - FORK_WORKFLOWS)} came from upstream: remove them (docs/fork.md)"
