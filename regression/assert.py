#!/usr/bin/env python3
"""Assertion engine — check one trajectory against one case's expectations.

Agent-agnostic: it only reads a session JSONL (via core/analyze.py), so a case
frozen from a Claude Code run asserts identically against a Codex or opencode
run. This is what makes the regression suite portable.

Supported expectations (all optional):
  terminates_clean : bool   run must (not) end with stop_reason "completed"
  no_repeated_tools: bool    no identical tool call fired more than once
  max_steps        : int     total loop steps
  max_tool_calls   : int
  max_llm_calls    : int
  max_tokens       : int
  max_cost         : float
  must_call        : [str]   these tools must appear
  must_not_call    : [str]   these tools must NOT appear
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "core"))
import analyze as A  # noqa: E402


def _tools(steps):
    return {s.get("tool_name") for s in steps if s.get("kind") == "tool_call"}


def check(steps: list[dict], expect: dict) -> list[dict]:
    a = A.analyze(steps)
    term, eff = a["termination"], a["efficiency"]
    tools = _tools(steps)
    results = []

    def add(name, ok, detail):
        results.append({"check": name, "passed": bool(ok), "detail": detail})

    if "terminates_clean" in expect:
        want = expect["terminates_clean"]
        add("terminates_clean", term["clean"] == want,
            f'stop_reason={term["stop_reason"]!r} (want clean={want})')
    if "no_repeated_tools" in expect and expect["no_repeated_tools"]:
        rep = eff["repeated_tool_calls"]
        add("no_repeated_tools", not rep,
            "none" if not rep else f'repeated: {[r["tool"] for r in rep]}')
    for key, field, label in [
        ("max_steps", "num_steps", "steps"),
        ("max_tool_calls", "num_tool_calls", "tool calls"),
        ("max_llm_calls", "num_llm_calls", "llm calls"),
        ("max_tokens", "total_tokens", "tokens"),
    ]:
        if key in expect:
            got = eff[field] or 0
            add(key, got <= expect[key], f"{got} {label} (max {expect[key]})")
    if "max_cost" in expect:
        got = eff["total_cost"] or 0
        add("max_cost", got <= expect["max_cost"], f"${got} (max ${expect['max_cost']})")
    if "must_call" in expect:
        missing = [t for t in expect["must_call"] if t not in tools]
        add("must_call", not missing, "all present" if not missing else f"missing: {missing}")
    if "must_not_call" in expect:
        hit = [t for t in expect["must_not_call"] if t in tools]
        add("must_not_call", not hit, "none called" if not hit else f"called: {hit}")
    return results


def load_case(path) -> dict:
    return json.loads(Path(path).read_text())


if __name__ == "__main__":
    # standalone: python assert.py <case.json> <trajectory.jsonl>
    case = load_case(sys.argv[1])
    steps = A.load(sys.argv[2])
    for r in check(steps, case.get("expect", {})):
        mark = "PASS" if r["passed"] else "FAIL"
        print(f'[{mark}] {r["check"]}: {r["detail"]}')
