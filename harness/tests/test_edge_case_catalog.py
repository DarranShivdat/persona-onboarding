"""The edge-case catalog is a contract: 31 cases, each mapped to >=1 tier (qa:fast)."""
import re
from pathlib import Path

import yaml

CATALOG = Path(__file__).resolve().parents[1] / "edge-cases.yaml"
TIERS = {"flow", "convo", "voice", "e2e", "live"}
CHANNELS = {"text", "voice", "both"}


def load():
    return yaml.safe_load(CATALOG.read_text())


def test_catalog_has_31_unique_cases():
    cases = load()["cases"]
    ids = [c["id"] for c in cases]
    assert len(cases) == 31
    assert len(set(ids)) == 31
    assert ids == [f"EC-{i:02d}" for i in range(1, 32)]


def test_every_case_is_complete_and_tiered():
    for c in load()["cases"]:
        for key in ("id", "title", "channel", "setup", "script", "expected", "tiers"):
            assert key in c, (c.get("id"), key)
        assert c["channel"] in CHANNELS, c["id"]
        assert c["tiers"] and set(c["tiers"]) <= TIERS, c["id"]
        assert c["script"], c["id"]
        assert "behavior" in c["expected"], c["id"]


def test_pending_engine_tests_reference_real_cases():
    ids = {c["id"] for c in load()["cases"]}
    src = (Path(__file__).resolve().parents[2] / "services/agent/tests/test_engine.py").read_text()
    for ref in re.findall(r"EC-\d\d", src):
        assert ref in ids, ref
