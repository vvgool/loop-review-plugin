#!/usr/bin/env python3
"""loop-review analysis — shared, LLM-free, stdlib only.

Reads a session's JSONL (produced by append.py) and reports the
loop-engineering signals raw observability skips: termination behavior,
repeated tool calls (going in circles), step/cost efficiency, and how the
assembled context evolved (when context was captured).

Usage:
    python analyze.py <session-id | path-to.jsonl> [--json]
    python analyze.py --list          # list captured sessions
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

STORE = Path(os.environ.get("LOOP_REVIEW_DIR", Path.home() / ".loop-review"))


def load(target: str) -> list[dict]:
    path = Path(target)
    if not path.exists():
        path = STORE / (target if target.endswith(".jsonl") else f"{target}.jsonl")
    if not path.exists():
        sys.exit(f"no capture found for: {target}")
    steps = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                steps.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    for i, s in enumerate(steps):
        s.setdefault("step_index", i)
    return steps


def analyze(steps: list[dict]) -> dict:
    tool_calls = [s for s in steps if s.get("kind") == "tool_call"]
    llm_steps = [s for s in steps if s.get("kind") == "llm_request"]
    stops = [s for s in steps if s.get("kind") == "stop"]
    stop_reason = stops[-1].get("stop_reason") if stops else None

    # repeated tool calls — the classic "stuck in a loop" smell
    sig, repeats = {}, []
    for s in tool_calls:
        k = f'{s.get("tool_name")}::{json.dumps(s.get("tool_input"), sort_keys=True)}'
        sig[k] = sig.get(k, 0) + 1
    for k, n in sig.items():
        if n > 1:
            repeats.append({"tool": k.split("::")[0], "count": n})

    # context evolution — needs captured context (content logging on)
    diffs, prev = [], None
    for s in llm_steps:
        cur = _keys(s.get("context"))
        if prev is not None and cur is not None:
            diffs.append({"step_index": s["step_index"],
                          "added": [x for x in cur if x not in prev],
                          "removed": [x for x in prev if x not in cur],
                          "size": len(cur)})
        if cur is not None:
            prev = cur

    tokens = sum((s.get("tokens_in") or 0) + (s.get("tokens_out") or 0) for s in steps)
    cost = sum(s.get("cost") or 0 for s in steps)
    return {
        "termination": {"stop_reason": stop_reason,
                        "clean": stop_reason == "completed",
                        "steps_to_stop": len(steps)},
        "efficiency": {"num_steps": len(steps), "num_tool_calls": len(tool_calls),
                       "num_llm_calls": len(llm_steps),
                       "repeated_tool_calls": sorted(repeats, key=lambda r: -r["count"]),
                       "total_tokens": tokens, "total_cost": round(cost, 4)},
        "context_diffs": diffs,
    }


def _keys(context):
    if not context:
        return None
    out = []
    for i, m in enumerate(context):
        if isinstance(m, dict):
            out.append(f'{i}:{m.get("role","?")}:{str(m.get("content",""))[:32]}')
        else:
            out.append(f"{i}:{str(m)[:40]}")
    return out


def report(a: dict) -> str:
    t, e = a["termination"], a["efficiency"]
    verdict = "clean stop" if t["clean"] else f'PROBLEM: {t["stop_reason"] or "no stop recorded"}'
    lines = [
        f'Termination : {verdict}  ({t["steps_to_stop"]} steps)',
        f'Efficiency  : {e["num_llm_calls"]} llm · {e["num_tool_calls"]} tool · '
        f'{e["total_tokens"]} tok · ${e["total_cost"]:.3f}',
    ]
    if e["repeated_tool_calls"]:
        for r in e["repeated_tool_calls"]:
            lines.append(f'  ! repeated: {r["tool"]} called {r["count"]}x (loop smell)')
    else:
        lines.append("  no repeated tool calls")
    if a["context_diffs"]:
        lines.append("Context     :")
        for d in a["context_diffs"]:
            adds = " ".join("+" + x for x in d["added"]) or "(no change)"
            lines.append(f'  step {d["step_index"]} (win={d["size"]}): {adds}')
    else:
        lines.append("Context     : not captured (enable content logging to diff)")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list or not args.target:
        if not STORE.exists():
            print("no sessions captured yet")
            return
        for f in sorted(STORE.glob("*.jsonl")):
            print(f.stem)
        return

    a = analyze(load(args.target))
    print(json.dumps(a, indent=2) if args.json else report(a))


if __name__ == "__main__":
    main()
