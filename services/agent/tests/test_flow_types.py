"""`npm run flow:types` emits TS types covering every slot, node and intent in flow.yaml."""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent.brain.spec import REPO_ROOT, load_spec


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_flow_types_generated_from_spec(tmp_path: Path):
    out = tmp_path / "flow-types.ts"
    r = subprocess.run(["node", str(REPO_ROOT / "scripts/flow-types.mjs"), "--out", str(out)],
                       capture_output=True, text=True, env={"PATH": __import__("os").environ["PATH"], "PERSONA_PYTHON": sys.executable})
    assert r.returncode == 0, r.stderr
    ts = out.read_text()
    spec = load_spec()
    for name in [*spec.slots, *spec.nodes, *spec.intents]:
        assert f'"{name}"' in ts, name
    assert "export type SlotName" in ts and "export type NodeId" in ts and "export type Intent" in ts
