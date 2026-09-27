"""qa:convo — scripted text conversations against the in-process turn loop (HARNESS-001)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "services" / "agent"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
