#!/usr/bin/env python3
"""PreToolUse guard for Claude Code workers in persona-onboarding.

Reads the hook JSON on stdin. Exit 2 (stderr shown to the model) blocks the call.
Enforced even under --dangerously-skip-permissions.

Policy (Darran, 2026-09-26):
  * penciled-emr is Darran's code. Workers MAY read/copy code from
    penciled-emr/voice-agent (prefer the sanitized mirror at
    ../persona-onboarding-ref/penciled-voice-agent), but must NEVER modify
    anything under penciled-emr (or the mirror).
  * Never read or copy .env*, transcripts/, data/, demo patient info,
    credentials, or anything with PHI.
  * Never git push.
"""
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
IDEA = os.path.join(HOME, "IdeaProjects")
PENCILED = os.path.join(IDEA, "penciled-emr")
VOICE = os.path.join(PENCILED, "voice-agent")
MIRROR = os.path.join(IDEA, "persona-onboarding-ref")

# Paths inside voice-agent that may contain PHI / demo patients / secrets.
SENSITIVE_IN_VOICE = (
    "transcripts", "data", "components", "flows", "scripts", "logs",
    "README.md", "KNOWN_ISSUES.md", "JUSTIN-WALKTHROUGH.md",
)
SENSITIVE_NAME = re.compile(
    r"(^|/)(\.env(?!\.example$)[^/]*|[^/]*credentials?\.(json|ya?ml|txt|csv)|secrets?\.(json|ya?ml|env|txt)|[^/]*\.pem|[^/]*\.p12|"
    r"id_rsa[^/]*|id_ed25519[^/]*|service[-_]account[^/]*\.json|token\.json)$",
    re.I,
)
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read", "Grep", "Glob"}


def block(msg):
    sys.stderr.write("BLOCKED by persona guard: " + msg + "\n")
    sys.exit(2)


def norm(p, cwd):
    if not p:
        return ""
    p = os.path.expanduser(str(p))
    if not os.path.isabs(p):
        p = os.path.join(cwd or os.getcwd(), p)
    try:
        return os.path.realpath(p)
    except Exception:
        return os.path.normpath(p)


def under(p, root):
    root = os.path.realpath(root)
    return p == root or p.startswith(root + os.sep)


def check_read_path(p):
    if SENSITIVE_NAME.search(p):
        block(f"reading secrets/credential files is forbidden ({os.path.basename(p)}).")
    if under(p, PENCILED):
        if not under(p, VOICE):
            block("only penciled-emr/voice-agent may be read (prefer the sanitized mirror "
                  "../persona-onboarding-ref/penciled-voice-agent).")
        rel = os.path.relpath(p, os.path.realpath(VOICE))
        first = rel.split(os.sep)[0]
        if first in SENSITIVE_IN_VOICE:
            block(f"penciled-emr/voice-agent/{first} may contain PHI/demo patient data/secrets.")
        if re.search(r"(emr|patient|pt_clinic|tools\.py|sms\.py)", rel, re.I):
            block(f"penciled-emr/voice-agent/{rel} touches EMR/patient data; use the mirror.")
    elif re.search(r"/IdeaProjects/penciled-(?!emr(/|$))", p):
        block("other penciled-* repositories are off limits.")


def check_write_path(p):
    if under(p, PENCILED) or re.search(r"/IdeaProjects/penciled-", p):
        block("NEVER modify anything in penciled-emr (read/copy only).")
    if under(p, MIRROR):
        block("the Penciled reference mirror is read-only.")
    if SENSITIVE_NAME.search(p):
        block(f"writing secret/credential files is forbidden ({os.path.basename(p)}).")


BASH_SENSITIVE = re.compile(
    r"(penciled-emr/voice-agent/(transcripts|data|components|flows|scripts|logs|README\.md|KNOWN_ISSUES\.md|JUSTIN-WALKTHROUGH\.md))"
    r"|(^|[\s/'\"=])\.env(?!\.example)(\b|$)"
    r"|/IdeaProjects/penciled-(?!emr)"
)


def check_bash(cmd):
    c = cmd or ""
    if re.search(r"\bgit\b[^;&|]*\bpush\b", c):
        block("git push is forbidden (local commits only).")
    if BASH_SENSITIVE.search(c):
        block("command references PHI/secret paths (.env, transcripts/, data/, demo patients, "
              "other penciled repos).")
    if "penciled-emr" in c and re.search(
            r"\b(cp|scp)\s+-[a-zA-Z]*[rRa]|\brsync\b|\bditto\b|\btar\b|\bzip\b|\bgrep\s+-[a-zA-Z]*[rR]|\brg\b|\bag\b|\bfind\b|\bdu\b|\bls\s+-[a-zA-Z]*R|\btree\b|\*", c):
        block("recursive/wildcard access to penciled-emr could sweep in transcripts/data/.env; "
              "read or copy individual files, or use the sanitized mirror "
              "../persona-onboarding-ref/penciled-voice-agent (recursive copy from the mirror is fine).")
    if "penciled-emr" in c or "persona-onboarding-ref" in c:
        # Allow read-only inspection and copying OUT; block anything that mutates there.
        mutators = r"(\brm\b|\bmv\b|\bchmod\b|\bchown\b|\btouch\b|\bsed\s+-i|\bperl\s+-[a-z]*i|\btee\b|\btruncate\b|"
        mutators += r"\bgit\s+(commit|checkout|reset|clean|stash|add|rm|mv|restore|switch|merge|rebase|pull|apply|am|cherry-pick|branch|tag)\b|"
        mutators += r"\bnpm\b|\bpip\b|\buv\b|\bpython3?\b[^|;&]*\bpenciled-emr|>\s*\S*(penciled-emr|persona-onboarding-ref))"
        if re.search(mutators, c):
            # cp/rsync FROM penciled into the repo is fine: allow when penciled appears only as source.
            block("command could modify penciled-emr or the reference mirror; only read/copy-out is allowed.")
        for m in re.finditer(r"\b(cp|rsync|ditto|install)\b([^;&|]*)", c):
            args = m.group(2).split()
            args = [a for a in args if not a.startswith("-")]
            if args and re.search(r"penciled-emr|persona-onboarding-ref", args[-1]):
                block("copy destination is inside penciled-emr / the mirror; copy INTO persona-onboarding only.")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    tool = data.get("tool_name", "")
    ti = data.get("tool_input", {}) or {}
    cwd = data.get("cwd") or os.getcwd()
    if tool == "Bash":
        check_bash(ti.get("command", ""))
        return 0
    paths = []
    for k in ("file_path", "path", "notebook_path"):
        if ti.get(k):
            paths.append(norm(ti[k], cwd))
    if tool == "Glob" and ti.get("pattern"):
        pat = ti["pattern"]
        if os.path.isabs(os.path.expanduser(pat)):
            paths.append(norm(pat.split("*")[0] or "/", cwd))
    if tool in ("Grep", "Glob"):
        base = norm(ti.get("path") or cwd, cwd)
        pat = str(ti.get("pattern", ""))
        if under(base, PENCILED) or "penciled-emr" in pat or (
                os.path.realpath(PENCILED).startswith(base + os.sep) and "penciled" in pat):
            block("Grep/Glob over penciled-emr could sweep in transcripts/data/.env; "
                  "search the sanitized mirror ../persona-onboarding-ref/penciled-voice-agent instead.")
    for p in paths:
        if tool in WRITE_TOOLS:
            check_write_path(p)
        check_read_path(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
