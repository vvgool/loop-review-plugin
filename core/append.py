#!/usr/bin/env python3
"""loop-review capture — shared across Claude Code, Codex, and opencode.

Reads one hook event as JSON on stdin, normalizes it to a unified step, and
appends it to ~/.loop-review/<session>.jsonl. No server, no network, stdlib only.

Claude Code and Codex share the same command-hook contract (JSON on stdin), so
the same script serves both — pass --source to tag which agent it is. opencode
uses an in-process JS plugin instead of command hooks; its adapter shells out to
this same script (see adapters/opencode/loop-review.ts).

Command hooks expect a pass-through response on stdout; we print {"continue":true}
so we never block or alter the agent loop.

Must stay runnable on Python 3.9 (macOS system python3) — hence the __future__
import; don't use runtime-evaluated PEP 604 unions outside annotations.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

STORE = Path(os.environ.get("LOOP_REVIEW_DIR", Path.home() / ".loop-review"))


def norm(source: str, p: dict) -> dict | None:
    """One event -> unified step, or None to skip. Fields read defensively."""
    # Claude Code and Codex use the same event names + stdin schema.
    if source in ("claude_code", "codex"):
        ev = p.get("hook_event_name") or p.get("event")
        tid = (p.get("session_id") or p.get("conversation_id")
               or (p.get("conversation") or {}).get("id") or "unknown")
        base = {"trace_id": str(tid), "tool_source": source,
                "ts": p.get("ts", time.time()), "raw": p}
        if ev in ("UserPromptSubmit",):
            return {**base, "kind": "user_prompt",
                    "tool_output": {"prompt": p.get("prompt")}}
        if ev in ("PostToolUse",):
            return {**base, "kind": "tool_call",
                    "tool_name": p.get("tool_name"),
                    "tool_input": p.get("tool_input"),
                    "tool_output": p.get("tool_response") or p.get("tool_output")
                                   or p.get("tool_result"),
                    "latency_ms": p.get("duration_ms")}
        if ev in ("Stop", "SubagentStop"):
            reason = p.get("stop_reason") or p.get("stopReason")
            if not reason:
                reason = "error" if p.get("error") else "completed"
            return {**base, "kind": "stop", "stop_reason": reason}
        return None  # PreToolUse / SessionStart / etc. — not distinct loop steps

    # opencode: the TS adapter already reshapes events into this simple envelope.
    if source == "opencode":
        kind = p.get("kind")
        if kind not in ("user_prompt", "tool_call", "stop", "llm_request"):
            return None
        return {"trace_id": str(p.get("session_id", "unknown")),
                "tool_source": "opencode", "ts": p.get("ts", time.time()),
                "kind": kind, "tool_name": p.get("tool_name"),
                "tool_input": p.get("tool_input"), "tool_output": p.get("tool_output"),
                "stop_reason": p.get("stop_reason"), "raw": p}
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="claude_code",
                    choices=["claude_code", "codex", "opencode"])
    args = ap.parse_args()

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    step = norm(args.source, payload)
    if step:
        try:
            STORE.mkdir(parents=True, exist_ok=True)
            f = STORE / f"{step['trace_id']}.jsonl"
            with open(f, "a") as fh:
                fh.write(json.dumps(step) + "\n")
        except Exception:
            pass  # never let capture break the agent

    # Pass-through response for command-hook agents (Claude Code / Codex).
    if args.source in ("claude_code", "codex"):
        print(json.dumps({"continue": True}))


if __name__ == "__main__":
    main()
