"""Regression tests for the loop-review core chain.

Covers the three things that actually break in practice:
  1. Capture: each agent's payload shape normalizes to the same unified step.
  2. Analyze: termination, repeated-tool (loop), and context-diff detection.
  3. Gate: the assertion engine passes clean runs and fails regressed ones.

Everything is stdlib + pytest, no network, no agent. Run: pytest -q
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
CORE = ROOT / "core"
sys.path.insert(0, str(CORE))
import analyze as A  # noqa: E402

APPEND = CORE / "append.py"


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ASRT = _load_module("assert_engine", ROOT / "regression" / "assert.py")


def capture(payload, source, store: Path):
    """Feed one event through append.py exactly as a hook would."""
    subprocess.run(
        [sys.executable, str(APPEND), "--source", source],
        input=json.dumps(payload), text=True, capture_output=True,
        env={"LOOP_REVIEW_DIR": str(store), "PATH": ""},
    )


def steps_of(store: Path, session):
    return A.load(str(store / f"{session}.jsonl"))


# ---------------------------------------------------------------- capture ---
@pytest.mark.parametrize("source,payload,session,kind", [
    ("claude_code",
     {"hook_event_name": "PostToolUse", "session_id": "cc",
      "tool_name": "Bash", "tool_input": {"cmd": "ls"}, "tool_response": {"ok": 1}},
     "cc", "tool_call"),
    ("codex",  # different id shape (conversation.id) + camelCase reason
     {"hook_event_name": "PostToolUse", "conversation": {"id": "cx"},
      "tool_name": "Read", "tool_input": {"p": "a"}, "tool_result": {"n": 3}},
     "cx", "tool_call"),
    ("opencode",  # pre-shaped envelope from the TS adapter
     {"kind": "tool_call", "session_id": "oc", "tool_name": "edit",
      "tool_input": {"f": "x.ts"}},
     "oc", "tool_call"),
])
def test_capture_normalizes_all_agents(tmp_path, source, payload, session, kind):
    capture(payload, source, tmp_path)
    steps = steps_of(tmp_path, session)
    assert len(steps) == 1
    assert steps[0]["kind"] == kind
    assert steps[0]["tool_source"] == source


def test_prompt_and_stop_are_captured(tmp_path):
    capture({"hook_event_name": "UserPromptSubmit", "session_id": "s", "prompt": "hi"},
            "claude_code", tmp_path)
    capture({"hook_event_name": "Stop", "session_id": "s", "stop_reason": "completed"},
            "claude_code", tmp_path)
    kinds = [s["kind"] for s in steps_of(tmp_path, "s")]
    assert kinds == ["user_prompt", "stop"]


def test_pretooluse_is_skipped_not_a_step(tmp_path):
    # PreToolUse fires but is not a distinct loop step
    capture({"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Bash"},
            "claude_code", tmp_path)
    assert not (tmp_path / "s.jsonl").exists()


def test_malformed_input_never_crashes(tmp_path):
    r = subprocess.run(
        [sys.executable, str(APPEND), "--source", "claude_code"],
        input="not json at all", text=True, capture_output=True,
        env={"LOOP_REVIEW_DIR": str(tmp_path), "PATH": ""},
    )
    assert r.returncode == 0  # must degrade gracefully, not error


# ---------------------------------------------------------------- analyze ---
def _traj(*steps):
    out = []
    for i, s in enumerate(steps):
        s.setdefault("step_index", i)
        out.append(s)
    return out


def test_clean_run_flagged_clean():
    t = _traj(
        {"kind": "user_prompt"},
        {"kind": "tool_call", "tool_name": "Edit", "tool_input": {"p": "a"}},
        {"kind": "stop", "stop_reason": "completed"},
    )
    a = A.analyze(t)
    assert a["termination"]["clean"] is True
    assert a["efficiency"]["repeated_tool_calls"] == []


def test_looping_run_detected():
    t = _traj(
        {"kind": "tool_call", "tool_name": "Bash", "tool_input": {"cmd": "pytest"}},
        {"kind": "tool_call", "tool_name": "Bash", "tool_input": {"cmd": "pytest"}},
        {"kind": "tool_call", "tool_name": "Bash", "tool_input": {"cmd": "pytest"}},
        {"kind": "stop", "stop_reason": "max_turns"},
    )
    a = A.analyze(t)
    assert a["termination"]["clean"] is False
    rep = a["efficiency"]["repeated_tool_calls"]
    assert rep and rep[0]["tool"] == "Bash" and rep[0]["count"] == 3


def test_same_tool_different_args_is_not_a_loop():
    t = _traj(
        {"kind": "tool_call", "tool_name": "Read", "tool_input": {"p": "a"}},
        {"kind": "tool_call", "tool_name": "Read", "tool_input": {"p": "b"}},
        {"kind": "stop", "stop_reason": "completed"},
    )
    assert A.analyze(t)["efficiency"]["repeated_tool_calls"] == []


def test_context_diff_tracks_growth():
    ctx1 = [{"role": "user", "content": "task"}]
    ctx2 = [{"role": "user", "content": "task"}, {"role": "tool", "content": "result"}]
    t = _traj(
        {"kind": "llm_request", "context": ctx1},
        {"kind": "llm_request", "context": ctx2},
        {"kind": "stop", "stop_reason": "completed"},
    )
    diffs = A.analyze(t)["context_diffs"]
    assert len(diffs) == 1
    assert diffs[0]["added"] and diffs[0]["size"] == 2


# ------------------------------------------------------------------- gate ---
CLEAN = _traj(
    {"kind": "user_prompt"},
    {"kind": "tool_call", "tool_name": "Edit", "tool_input": {"p": "a"}},
    {"kind": "stop", "stop_reason": "completed"},
)
REGRESSED = _traj(
    {"kind": "tool_call", "tool_name": "Bash", "tool_input": {"c": "x"}},
    {"kind": "tool_call", "tool_name": "Bash", "tool_input": {"c": "x"}},
    {"kind": "stop", "stop_reason": "max_turns"},
)
EXPECT = {"terminates_clean": True, "no_repeated_tools": True,
          "max_steps": 5, "must_call": ["Edit"]}


def test_gate_passes_clean_run():
    results = ASRT.check(CLEAN, EXPECT)
    assert all(r["passed"] for r in results)


def test_gate_fails_regressed_run():
    results = ASRT.check(REGRESSED, EXPECT)
    failed = {r["check"] for r in results if not r["passed"]}
    assert "terminates_clean" in failed
    assert "no_repeated_tools" in failed
    assert "must_call" in failed  # Edit never happened


def test_max_cost_and_budget_checks():
    t = _traj(
        {"kind": "llm_request", "cost": 0.4, "tokens_in": 100, "tokens_out": 50},
        {"kind": "stop", "stop_reason": "completed"},
    )
    assert ASRT.check(t, {"max_cost": 0.5})[0]["passed"] is True
    assert ASRT.check(t, {"max_cost": 0.3})[0]["passed"] is False


# --------------------------------------------------------------- end-to-end -
def test_full_chain_capture_to_gate(tmp_path):
    """Capture a looping run via append.py, then the gate must fail it."""
    sess = "e2e"
    events = [
        {"hook_event_name": "UserPromptSubmit", "session_id": sess, "prompt": "fix"},
        {"hook_event_name": "PostToolUse", "session_id": sess, "tool_name": "Bash",
         "tool_input": {"cmd": "pytest"}, "tool_response": {"exit": 1}},
        {"hook_event_name": "PostToolUse", "session_id": sess, "tool_name": "Bash",
         "tool_input": {"cmd": "pytest"}, "tool_response": {"exit": 1}},
        {"hook_event_name": "Stop", "session_id": sess, "stop_reason": "max_turns"},
    ]
    for e in events:
        capture(e, "claude_code", tmp_path)
    steps = steps_of(tmp_path, sess)
    results = ASRT.check(steps, {"terminates_clean": True, "no_repeated_tools": True})
    assert not all(r["passed"] for r in results)
