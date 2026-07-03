#!/usr/bin/env python3
"""Regression gate — run every case and fail CI on any regression.

Each case in cases/*.json is checked against a trajectory obtained one of three
ways:

  (default) replay : assert against the frozen reference cases/<id>.jsonl.
                     Zero agent, zero cost, fully self-contained — good as a
                     fast CI smoke gate and to verify the assertion engine.
  --session <dir>  : assert against live captures in a LOOP_REVIEW_DIR.
  --run            : re-run each agent on the case's task, capture a FRESH
                     trajectory, then assert. This is the real regression gate;
                     it needs the capture hooks installed and API access.

Exit code is non-zero if any case fails, so it drops straight into CI.

    python check.py                 # replay frozen references
    python check.py --run           # live re-run (needs agent + keys + hooks)
    python check.py --junit out.xml
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
import analyze as A  # noqa: E402
import importlib.util

# import assert.py (name isn't a valid identifier)
_spec = importlib.util.spec_from_file_location("assert_engine", Path(__file__).parent / "assert.py")
AS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(AS)

ROOT = Path(__file__).parent.parent
CASES = ROOT / "cases"


def trajectory_for(case: dict, mode: str, session_dir: str | None):
    cid = case["id"]
    if mode == "run":
        cmd = (case.get("run") or {}).get("cmd", "")
        if not cmd:
            return None, "case has no run.cmd (needed for --run)"
        cmd = cmd.replace("{task}", case.get("task", ""))
        with tempfile.TemporaryDirectory() as td:
            env = {**os.environ, "LOOP_REVIEW_DIR": td}
            try:
                subprocess.run(cmd, shell=True, env=env, timeout=600,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.TimeoutExpired:
                return None, "agent run timed out"
            files = sorted(Path(td).glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
            if not files:
                return None, "no trajectory captured (are capture hooks installed?)"
            return A.load(str(files[-1])), None
    if mode == "session":
        p = Path(session_dir or A.STORE) / f"{cid}.jsonl"
        if not p.exists():
            return None, f"no live session {cid}.jsonl in {p.parent}"
        return A.load(str(p)), None
    # replay
    p = CASES / f"{cid}.jsonl"
    if not p.exists():
        return None, f"no frozen reference {cid}.jsonl"
    return A.load(str(p)), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="re-run agents (live gate)")
    ap.add_argument("--session-dir", default=None)
    ap.add_argument("--junit", default=None)
    args = ap.parse_args()
    mode = "run" if args.run else ("session" if args.session_dir else "replay")

    case_files = sorted(CASES.glob("*.json"))
    if not case_files:
        print("no cases in cases/ — freeze one first (regression/freeze.py)")
        return 0

    all_pass, junit = True, []
    for cf in case_files:
        case = json.loads(cf.read_text())
        steps, err = trajectory_for(case, mode, args.session_dir)
        if err:
            all_pass = False
            print(f"✗ {case['id']}: {err}")
            junit.append((case["id"], [{"check": "trajectory", "passed": False, "detail": err}]))
            continue
        results = AS.check(steps, case.get("expect", {}))
        failed = [r for r in results if not r["passed"]]
        junit.append((case["id"], results))
        if failed:
            all_pass = False
            print(f"✗ {case['id']} ({mode})")
            for r in failed:
                print(f"    FAIL {r['check']}: {r['detail']}")
        else:
            print(f"✓ {case['id']} ({mode}) — {len(results)} checks")

    if args.junit:
        _write_junit(args.junit, junit)

    print("\n" + ("PASS — no regressions" if all_pass else "FAIL — regressions detected"))
    return 0 if all_pass else 1


def _write_junit(path, junit):
    import xml.sax.saxutils as x
    total = sum(len(r) for _, r in junit)
    fails = sum(1 for _, rs in junit for r in rs if not r["passed"])
    lines = [f'<testsuite name="loop-review" tests="{total}" failures="{fails}">']
    for cid, rs in junit:
        for r in rs:
            lines.append(f'  <testcase classname="{x.quoteattr(cid)[1:-1]}" name="{x.quoteattr(r["check"])[1:-1]}">')
            if not r["passed"]:
                lines.append(f'    <failure>{x.escape(r["detail"])}</failure>')
            lines.append("  </testcase>")
    lines.append("</testsuite>")
    Path(path).write_text("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
