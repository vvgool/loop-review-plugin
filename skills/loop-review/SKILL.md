---
name: loop-review
description: Review the current or a past agent-loop trajectory. Use when the user asks to review a session, check if the agent looped or went in circles, why a run stopped, how many steps/tokens it took, or to inspect a trajectory. Runs a local LLM-free analysis over captured session data.
---

# loop-review

Analyze a captured agent-loop trajectory and report the loop-engineering
signals: termination behavior (clean vs stuck), repeated tool calls (a "going
in circles" smell), step/token/cost efficiency, and context growth.

## How to run it

Sessions are captured by this plugin's hooks into `~/.loop-review/<session>.jsonl`.

To list captured sessions:

```
python3 "${CLAUDE_PLUGIN_ROOT}/core/analyze.py" --list
```

To analyze a specific session (use the id from the list, or the current
session id if known):

```
python3 "${CLAUDE_PLUGIN_ROOT}/core/analyze.py" <session-id>
```

Run the command, then present the report to the user in plain language:
call out whether the run terminated cleanly, flag any repeated tool calls as a
possible loop, and note the step/cost totals. If the user wants the full
step-by-step timeline, tell them to open `core/viewer.html` in a browser and
load the session's `.jsonl` file.
