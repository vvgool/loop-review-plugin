// loop-review — opencode adapter
//
// opencode doesn't use command hooks like Claude Code / Codex; it loads
// in-process JS/TS plugin modules that return a hooks object. So this thin
// adapter is the only part that differs across the three agents: it reshapes
// opencode's events into the SAME unified step and appends to the SAME store
// (~/.loop-review/<session>.jsonl) that core/analyze.py and core/viewer.html read.
//
// Install: drop this file in ~/.config/opencode/plugin/ (global) or
// .opencode/plugin/ (project). opencode loads it automatically at startup.
//
// NOTE: opencode's plugin hook signatures have shifted across versions, so we
// read fields defensively. If capture looks empty, log an event and adjust the
// field paths to match your installed opencode version.

import { appendFileSync, mkdirSync } from "fs";
import { homedir } from "os";
import { join } from "path";

const DIR = process.env.LOOP_REVIEW_DIR || join(homedir(), ".loop-review");

function write(step) {
  try {
    mkdirSync(DIR, { recursive: true });
    appendFileSync(join(DIR, `${step.trace_id}.jsonl`), JSON.stringify(step) + "\n");
  } catch (_) { /* never break the agent loop */ }
}

function sid(ctx, ev) {
  return (
    ev?.properties?.sessionId ||
    ev?.session_id ||
    ctx?.sessionID ||
    ctx?.session?.id ||
    "unknown"
  );
}

export const LoopReview = async (ctx) => {
  return {
    // one tool call completed
    "tool.execute.after": async (input, output) => {
      write({
        trace_id: String(input?.sessionID || ctx?.sessionID || "unknown"),
        tool_source: "opencode",
        ts: Date.now() / 1000,
        kind: "tool_call",
        tool_name: input?.tool ?? output?.tool,
        tool_input: output?.args ?? input?.args,
        tool_output: output?.result ?? output?.output,
        raw: { input, output },
      });
    },

    // user prompt + session termination come through the generic event stream
    event: async ({ event }) => {
      if (event?.type === "message.updated") {
        const m = event?.properties?.message;
        if (m?.role === "user") {
          write({
            trace_id: String(sid(ctx, event)),
            tool_source: "opencode",
            ts: Date.now() / 1000,
            kind: "user_prompt",
            tool_output: { prompt: m?.content },
            raw: event,
          });
        }
      }
      if (event?.type === "session.idle" || event?.type === "session.error") {
        write({
          trace_id: String(sid(ctx, event)),
          tool_source: "opencode",
          ts: Date.now() / 1000,
          kind: "stop",
          stop_reason: event.type === "session.error" ? "error" : "completed",
          raw: event,
        });
      }
    },
  };
};
