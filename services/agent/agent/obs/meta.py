"""Turn-trace metadata: node, flow version, prompt hash (ARCHITECTURE §10).

`prompts_hash` uses the same formula as the harness run name
(`<git-sha>|flow-v<N>|prompts-<hash>`), so a live trace can be matched to an eval run.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from ..brain.spec import FlowSpec

_cache: dict[int, tuple[FlowSpec, str]] = {}  # id -> (spec, hash); spec kept so ids aren't reused


def prompts_hash(spec: FlowSpec) -> str:
    hit = _cache.get(id(spec))
    if hit is not None and hit[0] is spec:
        return hit[1]
    from ..llm.prompts import extraction_system, phrasing_system
    from ..llm.schema import record_slots_tool

    blob = json.dumps([extraction_system(spec), phrasing_system(spec), record_slots_tool(spec)], sort_keys=True)
    h = hashlib.sha256(blob.encode()).hexdigest()[:10]
    _cache[id(spec)] = (spec, h)
    return h


def turn_metadata(spec: FlowSpec, node: str, **extra: Any) -> dict:
    meta: dict[str, Any] = {"node": node, "flow_version": spec.raw.get("version")}
    try:
        meta["prompt_hash"] = prompts_hash(spec)
    except Exception:  # noqa: BLE001 - metadata is best effort; never block a turn
        meta["prompt_hash"] = None
    meta.update({k: v for k, v in extra.items() if v is not None})
    return meta
