#!/usr/bin/env python3
"""Freeze a captured session into a regression case.

Turns "this run behaved correctly" into a committed guard: it derives an
expectation envelope from the observed run (clean termination, no loops, a step
/ cost ceiling with slack) and snapshots the trajectory as the reference.

Usage:
    python freeze.py <session-id> --id fix-failing-test \
        --task "Fix the failing test in test_auth.py" \
        --source claude_code [--slack 2] [--force]

Writes:
    cases/<id>.json    the case (task + expectations)
    cases/<id>.jsonl   the reference trajectory (for --replay in CI)

By default it refuses to freeze a run that did NOT terminate cleanly (freezing a
broken run as "golden" is almost always a mistake). Use --force to override, e.g.
to capture a known-bad trajectory as a negative fixture.
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
import analyze as A  # noqa: E402

CASES = Path(__file__).parent.parent / "cases"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("--id", required=True)
    ap.add_argument("--task", default="")
    ap.add_argument("--source", default="claude_code",
                    choices=["claude_code", "codex", "opencode"])
    ap.add_argument("--slack", type=int, default=2,
                    help="head-room added to observed step/tool ceilings")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    src_path = A.STORE / f"{args.session}.jsonl"
    if not src_path.exists():
        p = Path(args.session)
        src_path = p if p.exists() else src_path
    steps = A.load(str(src_path))
    a = A.analyze(steps)
    eff, term = a["efficiency"], a["termination"]

    if not term["clean"] and not args.force:
        sys.exit(f'refusing to freeze: run ended {term["stop_reason"]!r}, not clean. '
                 f'Use --force to freeze it anyway.')

    expect = {
        "terminates_clean": term["clean"],
        "no_repeated_tools": len(eff["repeated_tool_calls"]) == 0,
        "max_steps": eff["num_steps"] + args.slack,
        "max_tool_calls": eff["num_tool_calls"] + args.slack,
    }
    if eff["total_cost"]:
        expect["max_cost"] = round(eff["total_cost"] * 1.5 + 0.01, 4)

    case = {
        "id": args.id,
        "description": f"Frozen from session {args.session}",
        "source": args.source,
        "task": args.task,
        "run": {"cmd": ""},  # fill in to enable --run mode (see check.py)
        "expect": expect,
    }

    CASES.mkdir(exist_ok=True)
    (CASES / f"{args.id}.json").write_text(json.dumps(case, indent=2) + "\n")
    shutil.copy(src_path, CASES / f"{args.id}.jsonl")
    print(f"froze case '{args.id}':")
    print(json.dumps(expect, indent=2))


if __name__ == "__main__":
    main()
