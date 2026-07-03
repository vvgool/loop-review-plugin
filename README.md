# loop-review — cross-agent loop review plugin

A low-cost review layer for agent loops that works across **Claude Code**,
**Codex**, and **opencode**. It captures each loop's trajectory and reports the
loop-engineering signals raw observability skips: termination behavior,
repeated tool calls (going in circles), step/token/cost, and context growth.

## Why it's cheap

- **Capture costs zero tokens.** It runs at the harness level (hooks / plugin
  events), not in the model's context — no prompt tokens added.
- **Analysis uses no LLM.** Termination, loop, and efficiency checks are plain
  Python rules, run on demand.
- **No server.** Events append to local `~/.loop-review/<session>.jsonl`; you
  review with a CLI or a static HTML file. Nothing stays running.

## One core, thin adapters

Codex deliberately adopted Claude Code's hook contract (same event names, same
JSON-on-stdin protocol), so **Claude Code and Codex share the exact same capture
script**. Only opencode differs — it loads in-process JS plugins instead of
command hooks — so it gets a small TS shell that writes into the same store.

```
core/                    shared, agent-agnostic
  append.py              stdin event -> unified step -> JSONL   (stdlib only)
  analyze.py             JSONL -> termination / loop / cost report
  viewer.html            static timeline + context-diff viewer (no server)

.claude-plugin/, hooks/, skills/   Claude Code plugin (repo root IS the plugin)
codex/                   Codex registration (config.toml or bundled hooks.json)
opencode/                opencode JS plugin (writes the same JSONL)
```

## Install

### Claude Code
The repo root is the plugin. Local dev:
```bash
claude --plugin-dir /ABS/PATH/loop-review-plugin
```
Or publish via a marketplace (`.claude-plugin/marketplace.json` in a git repo)
and `/plugin install loop-review@<marketplace>`. Hooks register automatically;
`/loop-review` becomes available as a slash command.

### Codex
Point Codex at the shared capture script — see `codex/README.md` for the
`~/.codex/config.toml` snippet. Then run `/hooks` in Codex to **trust** the hook
(Codex skips untrusted hooks by design).

### opencode
Copy the plugin into opencode's plugin directory:
```bash
cp opencode/loop-review.ts ~/.config/opencode/plugin/
```
opencode loads it at startup. It writes to the same `~/.loop-review/` store.

## Review a session

Works the same regardless of which agent produced the run:
```bash
python3 core/analyze.py --list           # list captured sessions
python3 core/analyze.py <session-id>     # text report
python3 core/analyze.py <session-id> --json
```
For the full step-by-step timeline with context diffs, open `core/viewer.html`
in a browser and load the session's `.jsonl` file. In Claude Code you can also
just type `/loop-review`.

## What you get for free vs. what needs opting in

Hook/event capture gives tool calls, prompts, and termination at zero token
cost. The **context-diff view and step replay need the assembled context**,
which agents don't log by default (privacy). To enable those, turn on content
logging on the agent (e.g. Claude Code / Codex `OTEL_LOG_RAW_API_BODIES`) and
feed those bodies in as `llm_request` steps. Everything else works out of the box.

## Verify the install

After installing on any agent, confirm the whole chain actually works:
```bash
python doctor.py
```
It pushes a synthetic event through capture → store → analyze (proving the core),
checks each agent's wiring, and — most importantly — reports whether any *real*
sessions have been captured, which is the only proof your hooks are firing during
real runs. Exit code is non-zero only if the core self-test fails.

Run the unit tests before shipping any change to the core:
```bash
pytest -q          # 14 tests covering capture, analyze, and the gate
```

## Regression gate (turns the viewer into a guard)

`analyze.py` tells you a run looped or terminated badly. The `regression/` layer
freezes that judgment into a committed test so the bug can't come back — and
because it only eats JSONL, one case guards all three agents.

**Freeze a known-good run into a case:**
```bash
python regression/freeze.py <session-id> --id add-rate-limiter \
    --task "add a rate limiter to api.py" --source claude_code
```
This derives an expectation envelope (clean termination, no repeated tools, a
step/cost ceiling with slack) and snapshots the trajectory. It refuses to freeze
a run that didn't terminate cleanly unless you pass `--force`.

Cases live in `cases/<id>.json` and are plain, hand-editable:
```json
{
  "expect": {
    "terminates_clean": true,
    "no_repeated_tools": true,
    "max_steps": 6,
    "max_tool_calls": 4,
    "must_call": ["Edit"],
    "must_not_call": []
  }
}
```

**Run the gate (exits non-zero on any regression):**
```bash
python regression/check.py            # replay frozen references — zero cost, no agent
python regression/check.py --run      # re-run agents live, assert fresh runs (needs hooks + keys)
python regression/check.py --junit report.xml
```

`--run` is the real gate: it sets a temp `LOOP_REVIEW_DIR`, executes each case's
`run.cmd` (e.g. `claude -p "{task}"`, `codex exec "{task}"`, `opencode run
"{task}"`), and asserts the fresh trajectory. `.github/workflows/loop-regression.yml`
wires both modes into CI: the zero-cost replay gate on every PR and push, the
live gate on manual dispatch (fill in the workflow's agent-install step first —
a bare runner has no agent CLI or API key).

## Beyond this

Add a trajectory-level LLM judge for decision coherence (the one place an LLM
call earns its cost), and extend the assertion vocabulary as you discover new
failure modes. The engine is a single function in `regression/assert.py`.
