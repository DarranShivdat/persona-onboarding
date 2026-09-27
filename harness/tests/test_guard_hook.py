"""Tests for scripts/guard-tool-use.py (Claude Code PreToolUse guard)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD = ROOT / "scripts" / "guard-tool-use.py"
IDEA = os.path.join(os.path.expanduser("~"), "IdeaProjects")
P = os.path.join(IDEA, "penciled-emr", "voice-agent")
M = os.path.join(IDEA, "persona-onboarding-ref", "penciled-voice-agent")
REPO = str(ROOT)


def run(tool, **ti):
    r = subprocess.run([sys.executable, str(GUARD)],
                       input=json.dumps({"tool_name": tool, "tool_input": ti, "cwd": REPO}),
                       capture_output=True, text=True)
    return "block" if r.returncode == 2 else "allow"


CASES = [
    ("allow", "Read", {"file_path": f"{P}/bot.py"}),
    ("allow", "Read", {"file_path": f"{M}/flow_engine.py"}),
    ("block", "Read", {"file_path": f"{P}/.env"}),
    ("block", "Read", {"file_path": f"{P}/transcripts/a.json"}),
    ("block", "Read", {"file_path": f"{P}/data/x.csv"}),
    ("block", "Read", {"file_path": f"{P}/README.md"}),
    ("block", "Read", {"file_path": f"{P}/tools.py"}),
    ("block", "Read", {"file_path": f"{IDEA}/penciled-emr/backend/app.py"}),
    ("block", "Edit", {"file_path": f"{P}/bot.py"}),
    ("block", "Write", {"file_path": f"{M}/new.py"}),
    ("allow", "Write", {"file_path": f"{REPO}/services/agent/agent/voice/bot.py"}),
    ("allow", "Write", {"file_path": f"{REPO}/.env.example"}),
    ("block", "Read", {"file_path": f"{REPO}/.env.local"}),
    ("block", "Grep", {"pattern": "x", "path": f"{P}/transcripts"}),
    ("block", "Grep", {"pattern": "x", "path": P}),
    ("allow", "Grep", {"pattern": "x", "path": M}),
    ("allow", "Bash", {"command": f"cp {M}/echo_guard.py services/agent/agent/voice/"}),
    ("allow", "Bash", {"command": f"cp -R {M}/ services/agent/agent/voice/ref/"}),
    ("allow", "Bash", {"command": f"cp {P}/flow_engine.py services/agent/agent/voice/"}),
    ("block", "Bash", {"command": f"cp -r {P} ./ref"}),
    ("block", "Bash", {"command": f"grep -rn Cartesia {P}"}),
    ("block", "Bash", {"command": f"cat {P}/*.py"}),
    ("block", "Bash", {"command": f"cat {P}/.env"}),
    ("block", "Bash", {"command": f"ls {P}/transcripts"}),
    ("block", "Bash", {"command": f"sed -i '' s/a/b/ {P}/bot.py"}),
    ("block", "Bash", {"command": f"cd {P} && git checkout -b x"}),
    ("block", "Bash", {"command": f"cp foo.py {P}/"}),
    ("block", "Bash", {"command": "git push origin main"}),
    ("allow", "Bash", {"command": "git commit -m wip && npm run qa:fast"}),
    ("block", "Bash", {"command": "cat .env"}),
]


@pytest.mark.parametrize("expect,tool,ti", CASES)
def test_guard(expect, tool, ti):
    assert run(tool, **ti) == expect


def test_settings_wires_guard():
    s = json.loads((ROOT / ".claude" / "settings.json").read_text())
    cmds = [h["command"] for e in s["hooks"]["PreToolUse"] for h in e["hooks"]]
    assert any("guard-tool-use.py" in c for c in cmds)
    deny = s["permissions"]["deny"]
    assert any(d.startswith("Edit(//") and "penciled-emr" in d for d in deny)
    assert "Bash(git push:*)" in deny
