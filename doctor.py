#!/usr/bin/env python3
"""loop-review doctor — verify an install actually works.

Run this after installing on any agent. It does three things:
  1. Self-test: pushes a synthetic event through append.py and confirms it
     lands in the store and analyzes correctly. This proves the core chain.
  2. Install check: looks for each agent's registration wiring (best-effort;
     you only need the agent(s) you actually use).
  3. Live check: counts real captured sessions — the only proof that hooks are
     actually firing during real runs.

    python doctor.py

Exit code is non-zero only if the core self-test fails (a broken core is a hard
error; a missing agent you don't use is just informational).
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parent
CORE = ROOT / "core"
STORE = Path(os.environ.get("LOOP_REVIEW_DIR", Path.home() / ".loop-review"))

G, Y, R, X = "\033[32m", "\033[33m", "\033[31m", "\033[0m"
if not sys.stdout.isatty():
    G = Y = R = X = ""


def line(status, msg, detail=""):
    color = {"PASS": G, "WARN": Y, "FAIL": R, "INFO": ""}[status]
    tag = {"PASS": "✓", "WARN": "!", "FAIL": "✗", "INFO": "·"}[status]
    print(f"  {color}{tag}{X} {msg}" + (f"  {detail}" if detail else ""))


def self_test() -> bool:
    print("\ncore self-test")
    for f in ("append.py", "analyze.py"):
        if not (CORE / f).exists():
            line("FAIL", f"core/{f} missing")
            return False
    line("PASS", "core scripts present")

    with tempfile.TemporaryDirectory() as td:
        env = {**os.environ, "LOOP_REVIEW_DIR": td}
        events = [
            {"hook_event_name": "PostToolUse", "session_id": "_doctor",
             "tool_name": "Bash", "tool_input": {"cmd": "x"}, "tool_response": {"e": 1}},
            {"hook_event_name": "PostToolUse", "session_id": "_doctor",
             "tool_name": "Bash", "tool_input": {"cmd": "x"}, "tool_response": {"e": 1}},
            {"hook_event_name": "Stop", "session_id": "_doctor", "stop_reason": "max_turns"},
        ]
        for e in events:
            r = subprocess.run([sys.executable, str(CORE / "append.py"),
                                "--source", "claude_code"],
                               input=json.dumps(e), text=True,
                               capture_output=True, env=env)
            if r.returncode != 0:
                line("FAIL", "append.py errored on a valid event", r.stderr.strip()[:80])
                return False
        f = Path(td) / "_doctor.jsonl"
        if not f.exists():
            line("FAIL", "capture produced no file — store not writable?")
            return False
        n = len(f.read_text().splitlines())
        line("PASS", f"capture wrote {n} events to the store")

        out = subprocess.run([sys.executable, str(CORE / "analyze.py"), str(f)],
                             capture_output=True, text=True, env=env)
        if "repeated: Bash" not in out.stdout or "max_turns" not in out.stdout:
            line("FAIL", "analyze.py did not detect the seeded loop", out.stdout[:80])
            return False
        line("PASS", "analyze detected termination + loop correctly")
    return True


def store_check():
    print("\nstore")
    try:
        STORE.mkdir(parents=True, exist_ok=True)
        t = STORE / ".doctor_write_test"
        t.write_text("ok"); t.unlink()
        line("PASS", f"store writable", str(STORE))
    except Exception as e:
        line("FAIL", f"store not writable: {STORE}", str(e))


def install_check():
    print("\nagent wiring (only the ones you use need to pass)")

    # Claude Code — plugin files + optional settings reference
    hj = ROOT / "hooks" / "hooks.json"
    if hj.exists():
        try:
            json.loads(hj.read_text())
            line("PASS", "Claude Code: plugin hooks.json present and valid")
        except json.JSONDecodeError:
            line("FAIL", "Claude Code: hooks.json is invalid JSON")
    ref = _grep_settings([
        Path.home() / ".claude" / "settings.json",
        Path.cwd() / ".claude" / "settings.json",
    ], "append.py")
    line("PASS" if ref else "INFO",
         "Claude Code: hook referenced in settings" if ref
         else "Claude Code: not found in your settings (fine if using --plugin-dir or unused)")

    # Codex — config.toml reference; remind about trust
    codex_ref = _grep_settings([Path.home() / ".codex" / "config.toml"], "append.py")
    if codex_ref:
        line("PASS", "Codex: hook referenced in ~/.codex/config.toml")
        line("INFO", "Codex: run /hooks in Codex to TRUST it, or it stays skipped")
    else:
        line("INFO", "Codex: not configured (see codex/README.md if you use Codex)")

    # opencode — plugin file installed
    oc = [Path.home() / ".config" / "opencode" / "plugin" / "loop-review.ts",
          Path.cwd() / ".opencode" / "plugin" / "loop-review.ts"]
    if any(p.exists() for p in oc):
        line("PASS", "opencode: plugin installed in plugin dir")
    else:
        line("INFO", "opencode: plugin not installed (cp opencode/loop-review.ts to "
                     "~/.config/opencode/plugin/ if you use opencode)")


def _grep_settings(paths, needle):
    for p in paths:
        try:
            if p.exists() and needle in p.read_text():
                return True
        except Exception:
            pass
    return False


def live_check():
    print("\nlive captures (proof hooks are actually firing)")
    sessions = sorted(STORE.glob("*.jsonl")) if STORE.exists() else []
    real = [s for s in sessions if s.stem != "_doctor"]
    if not real:
        line("INFO", "no real sessions captured yet — run your agent on a task, "
                     "then re-run doctor")
        return
    line("PASS", f"{len(real)} session(s) captured")
    newest = max(real, key=lambda p: p.stat().st_mtime)
    out = subprocess.run([sys.executable, str(CORE / "analyze.py"), str(newest)],
                         capture_output=True, text=True)
    first = out.stdout.splitlines()[0] if out.stdout else ""
    line("INFO", f"most recent: {newest.stem}", first)


def main():
    print("loop-review doctor")
    ok = self_test()
    store_check()
    install_check()
    live_check()
    print()
    if ok:
        line("PASS", "core is healthy — capture, store, and analysis all work")
        return 0
    line("FAIL", "core self-test failed — see above; the tool won't work until fixed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
