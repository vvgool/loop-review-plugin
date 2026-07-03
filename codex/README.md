# Codex adapter

Codex uses the **same hook contract as Claude Code** (JSON on stdin → your
command → JSON on stdout), with the same event names — so the shared
`core/append.py` works unchanged. You register it in one of two ways.

## Option A — inline in `~/.codex/config.toml` (simplest)

Add these tables, replacing `/ABS/PATH` with this repo's absolute path:

```toml
[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = 'python3 "/ABS/PATH/loop-review-plugin/core/append.py" --source codex'
timeout = 5

[[hooks.PostToolUse]]
matcher = "*"
[[hooks.PostToolUse.hooks]]
type = "command"
command = 'python3 "/ABS/PATH/loop-review-plugin/core/append.py" --source codex'
timeout = 5

[[hooks.Stop]]
[[hooks.Stop.hooks]]
type = "command"
command = 'python3 "/ABS/PATH/loop-review-plugin/core/append.py" --source codex'
timeout = 5
```

## Option B — as a bundled plugin

Ship `hooks/hooks.json` (in this folder) inside a Codex plugin; Codex sets
`CLAUDE_PLUGIN_ROOT` so the relative path resolves.

## Trust the hook

Codex does **not** auto-trust non-managed hooks. On next start it will flag the
new hook for review — run `/hooks` in the Codex TUI to inspect and trust it.
Until trusted, the hook is skipped (this is a safety feature, not a bug).

## Review captured sessions

Same as the other agents:

```bash
python3 core/analyze.py --list
python3 core/analyze.py <session-id>
```
