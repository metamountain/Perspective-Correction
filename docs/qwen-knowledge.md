# Qwen sub-agent knowledge — consolidated (2026-09-13)

Everything learned about the local Qwen3.8-27B worker this session, in one place.
Saved because delegation to it is paused as of 2026-09-13 (too slow this session;
the architect is implementing directly for now) — this is the record of what was
configured and learned, so restarting delegation later doesn't mean re-discovering
it. Full narrative detail, sources and debugging notes:
`docs/worker-environment.md`. Delegation method and lessons: `skills/delegation.md`.

## Status

**Revised same day.** Full OpenCode-orchestrated delegation (write a package, wait
on a slow agentic session, independently re-verify everything against the repo)
was paused a few hours into 2026-09-13 — not because Qwen's output was bad, but
because the architect's own *verification* overhead (recurring drift checks,
re-reading a concurrently-edited CLAUDE.md, full suite re-runs to catch a stale
"0 failed" claim) regularly cost more than doing the change directly. See
"Delegation economics" below for the concrete break-even case and the gate this
led to. **Mechanism confirmed 2026-09-13** via `unsloth start claude --as-subagent
--model unsloth/Qwen3.8-27B-GGUF:UD-IQ4_XS --no-launch` (prints the launch
command instead of running it): it's a **Claude Code plugin**, not a separate
process —
```
claude --plugin-dir '~/.unsloth/studio/auth/agents/claude-subagent/unsloth-local-agent'
       --allowedTools=mcp__plugin_unsloth-local-agent_unsloth__unsloth_agent,
                       mcp__plugin_unsloth-local-agent_unsloth__unsloth_plan_agent
```
Registers two MCP tools, `unsloth_agent` and `unsloth_plan_agent` — callable
*in-session*, hand a task to Qwen and get a result back in the same turn. No
separate OpenCode process, no concurrently-edited CLAUDE.md to reconcile, no
30-minute drift check needed — a genuinely different cost shape than the
OpenCode flow this session used, **if** `unsloth_agent`'s own per-call cost
turns out reasonable (not yet measured — first real call is the next step).
**Still a launch-time flag** — only available to a session started (or resumed)
with it; cannot be applied to an already-running session.

## Delegation economics — the cost that matters is the architect's, not Qwen's

"It cost more to delegate" (user, 2026-09-13) means the **architect's** tokens,
not Qwen's. Qwen's own cost is known and roughly fixed: 100–140k tokens per
package (measured: 108k, 126k, 137k — `skills/delegation.md`), ~22:1 input:output,
dominated by exploration re-prefill (one sampled session: 10× `read` + 2× `grep`
on the same file, zero edits — `worker-environment.md`). That side of the ledger
is already optimized (low reasoning effort, exploration handed over pre-packaged).

The side that actually blew the budget was verification: a 30-minute recurring
drift check across many cycles, re-reading CLAUDE.md after every concurrent edit,
two full ~150s suite re-runs that were needed to catch a self-reported "0 failed"
that was actually 4 failed, and a deep git-diff/grep audit to recover several
undocumented shipped features. None of that scales down with a better prompt to
Qwen — it's a property of *not trusting a report without re-deriving it*.

**Concrete break-even case, from this session**: renaming one label
(`text="source"` → `text="mask"`, `gui.py:1783`) was handed to Qwen as a one-line,
exact-location Open item. It sat unpicked-up across **four** 30-minute recurring
checks — each cycle cost one grep to confirm it still hadn't landed. Doing it
directly: two tool calls (Grep to confirm the line, Edit to change it), done.
Four checks' worth of verification overhead for a task an architect can finish
faster than writing the package. This is the shape of every low-value delegation
this session, not a one-off.

**The gate that follows** — delegate only when a task clears all three:
1. **Needs more exploration than can be pre-packaged.** If the exact file/line is
   already known (as it always should be per `skills/delegation.md` lesson 9 —
   "never make the worker grep for a name"), there is nothing left for
   delegation to buy; doing it directly is the same work minus the round trip.
2. **Produces enough change to amortize a verification pass.** A verification
   pass (suite run + ledger reconciliation) costs roughly the same whether the
   diff is 5 lines or 500. Batch related Open items into one package rather than
   delegating each separately — this session's biggest single win (the `btns`
   row + FIND-controls-relocation + window-floor batch) paid for its own
   verification; the one-line rename never could.
3. **Has a mechanical verifier** — a named test, or a command with an
   unambiguous pass/fail — so verification is one command, not a re-derivation
   from git diffs and greps. Every "wrong done" this session (the line-brush
   toggle→erase mislabel, the stale "0 failed" header) was caught by exactly
   this kind of check; every expensive verification cycle was one *without* a
   crisp check to run, forcing a manual audit instead.

Fail any of the three and the honest default is: do it directly.

## Why Qwen Code beat OpenCode as the harness (2026-09-14)

Same machine, same local model family, very different results — the user reported
Qwen Code working "much better". It is not mysterious; the two configs disagree on
four things and three of them matter.

| | **Qwen Code** (`~/.qwen/settings.json`) | **OpenCode** (`~/.config/opencode/opencode.jsonc`) |
|---|---|---|
| endpoint | `http://127.0.0.1:8888/v1` — **live** (401, wants a key) | `http://192.168.188.114:8001/v1` — **dead, nothing listens** |
| model | `unsloth/Qwen3.8-27B-GGUF` | `unsloth/Qwen3.6-27B-MTP` — a generation older |
| thinking | **off** (`extra_body.enable_thinking: false`) | **on** (`chat_template_kwargs.enable_thinking: true`) |
| sampling | `temperature 0.6`, `max_tokens 8192`, timeout 300 s, 1 retry | `temperature 0.3`, `top_p 0.95`, `top_k 40`, `min_p 0.01`, `presence_penalty 0.1`, **no `max_tokens`** |

1. **The endpoint is wrong in OpenCode's config.** The LAN IP is this machine and
   is correct; the *port* is not — Unsloth Studio serves 8888, the config says
   8001, and 8001 refuses the connection (verified with curl: `000` vs `401`). It
   worked at all only because `unsloth start opencode` points the agent at the
   running server and overrides the file. Launch plain `opencode` and it is
   talking to nothing. Check this first if OpenCode ever "hangs" or dies at start.
2. **Thinking off is very likely why it felt better.** Qwen Code disables it
   outright. Every agentic round trip otherwise pays for a reasoning block, and
   OpenCode sets **no `max_tokens`** at all — which is exactly the documented trap
   below ("a thinking model with a small budget returns empty `content` and a full
   `reasoning_content`"). Reproduced directly on this box: a 20-token request to
   the local server spent the whole allowance reasoning and came back with a
   synthetic "no budget left for an answer" message instead of output. Qwen Code
   pins `max_tokens: 8192`, so it has room for both.
3. **The model is a generation older on the OpenCode side** — 3.6-MTP vs 3.8.
   This is the same CLAUDE.md-vs-`opencode.jsonc` discrepancy flagged on 09-13
   and never resolved; this is it biting.
4. Sampling differs too (T 0.3 vs 0.6 and friends) but is the least likely cause:
   the project's own bench swept T=0.3..1.0 and found every task passed at every
   point.

**To align OpenCode with what works**: model → `unsloth/Qwen3.8-27B-GGUF`,
baseURL → `http://127.0.0.1:8888/v1`, `enable_thinking` → false, add
`max_tokens: 8192`. Thinking-off is a genuine behavioural choice rather than a
straight bug fix, so decide it deliberately rather than copying it blindly.

## Model & server

- Model: `unsloth/Qwen3.8-27B-GGUF` (`UD-IQ4_XS` quant), 13.3 GB GGUF + 0.9 GB
  mmproj, served via Unsloth Studio → llama-server.
- CLAUDE.md's documented endpoint: `http://127.0.0.1:8888/v1`.
- **Unreconciled discrepancy, flagged but never resolved**: the actual
  `opencode.jsonc` at one point pointed to a *different* model/server —
  `unsloth/Qwen3.6-27B-MTP` at `http://192.168.188.114:8001/v1`, temperature 0.3,
  top_k 40 — not what CLAUDE.md's Worker section describes. Never got a user
  decision on which is authoritative. **Check `opencode.jsonc` fresh before
  resuming delegation** rather than trusting either old record.
- GPU: NVIDIA RTX 4090, 24 GiB.

### Context is not a constant — it is decided per load, by free VRAM

This is the number most worth re-checking rather than remembering, because it
moves between launches on the same machine with the same model:

| load | free VRAM at start | context granted |
|---|---|---|
| 2026-09-13, earlier | 24,138 MiB | **96,256** (94,848 usable) |
| 2026-09-13, later | 19,275 MiB | **39,424** |

Same GGUF, same box; the model's *native* context is 262,144 and neither figure
is it. Unsloth Studio sizes the KV cache to what is free and logs the decision —
`"Context auto-reduced: 262144 -> 39424 (model: 13.3 GB, est. KV cache: 3.0 GB)"`
in `%TEMP%\unsloth-start-server-*.log`. So anything else holding VRAM when the
server starts (a stray test worker, a browser, ComfyUI) silently costs context
for the whole session.

**Read it, don't assume it**: `GET /v1/models` reports `context_length`,
`max_context_length` and `native_context_length` per model. CLAUDE.md's Worker
section still says 94,848 — true when measured, wrong for the 39,424 load.

Why it matters for the harness comparison above: at 39,424 with Qwen Code's
`max_tokens: 8192` pinned for output, roughly **31k is left for system prompt,
tools and history** — and an agentic CLI re-prefills all of it on every tool
round trip. That is a real working budget, not a generous one. OpenCode setting
no `max_tokens` at all is worse than untidy here: the output side is unbounded
against a context that may have quietly halved since the last launch.

## Sampling

| | temperature | top_p | top_k | presence_penalty |
|---|---|---|---|---|
| thinking | 1.0 | 0.95 | 20 | 0 |
| non-thinking | 0.7 | 0.8 | 20 | 1.5 |

Never temperature 0 (Qwen3 degrades into repetition). `max_tokens` ≥ 32768 — a
small budget on a thinking model returns empty `content` with a full
`reasoning_content`, which looks like a broken endpoint but isn't.

**Decided default**: `reasoning_effort=low` for packages (~25% fewer completion
tokens than medium, equal pass rate); escalate to `medium` only after a package
fails twice; `/no_think` for purely mechanical sub-steps.

## Chat template

Custom build, self-identifies as `"qwen3.8-froggeric-v22.5"` — not stock Qwen3.
Lives at `C:\Users\<user>\Desktop\chat_template.jinja` (edited this session:
`preserve_thinking` default → `false`, `reasoning_effort` fallback → `low`, to
match the decided default instead of silently defeating it).

**Important, easy to miss**: the llama-server *launch command* itself (built by
Unsloth Studio, visible in `%TEMP%\unsloth-start-server-*.log`) passes its own
`--chat-template-kwargs {"enable_thinking": true, "preserve_thinking": true}` —
this can shadow the template file's own internal default for any request that
doesn't explicitly override it. Editing the `.jinja` file alone is not guaranteed
to change live behaviour; check the actual launch command's `--chat-template-kwargs`
too.

Strict thinking-off is real and per-request, two ways: `reasoning_effort: "off"`
in `chat_template_kwargs`, or a literal `<|think_off|>` tag in the message text
(stripped before reaching the model — a hard override, not `/no_think`'s soft hint).

## Known bugs (both confirmed, neither is this project's)

- **`reasoning_tokens` always reports 0** in `usage.completion_tokens_details` —
  confirmed llama.cpp server bug (ggml-org/llama.cpp#22659,
  QwenLM/qwen-code#7236). Thinking genuinely happens and `reasoning_content` is
  correct; only its *token count* is never populated. Harmless, just don't read
  "Reasoning: 0" as "no thinking happened."
- **`opencode-token-monitor` plugin crashes on Windows** — `saveSessionRecord`
  (`lib/history.ts` → `dist/plugin.js:~486`) builds a path with `path.join`
  (backslash on Windows) then does `.lastIndexOf("/")` to find its directory —
  finds nothing, `mkdirSync("")` throws `ENOENT`, fatal inside the `session.idle`
  handler, kills the whole OpenCode session. **Patched locally** (added
  `dirname` to the `path` import, used it instead of the slash-search) at
  `C:\Users\<user>\.cache\opencode\packages\opencode-token-monitor@latest\
  node_modules\opencode-token-monitor\dist\plugin.js`. **Will silently revert**
  when the `@latest`-pinned plugin re-resolves — re-check this file if the same
  crash comes back. Upstream duplicate already open and unanswered since March:
  github.com/Ainsley0917/opencode-token-monitor/issues/1 — a confirming comment
  with the fix was posted there 2026-09-13.

## Launcher

`C:\Users\<user>\Desktop\opencode_bpc.bat`:
```
@echo off
title opencode
color 1F
cd /d "D:\Coding\Batch-Perspective-Correction"
unsloth start opencode --model unsloth/Qwen3.8-27B-GGUF:UD-IQ4_XS --reasoning-effort medium
cmd /k
```
The `cd /d` line is the real fix for a long-standing startup failure: a
double-clicked `.bat` runs with its own folder (Desktop) as cwd, not the project,
which broke `opencode` silently. `--reasoning-effort medium` here predates the
`low` default decided later — never reconciled back into this file.

## Where token/reasoning content actually lives

`C:\Users\<user>\.local\share\opencode\opencode.db` (SQLite, live). Aggregate
per-session token counts: table `session`, columns `tokens_input` /
`tokens_output` / `tokens_reasoning` / `tokens_cache_read` / `tokens_cache_write`.
**Full reasoning text**, not just counts: table `part`, JSON `data` column,
`type: "reasoning"` rows carry the actual `<think>` text — plain, queryable,
no setup needed. This is what let the architect review Qwen's actual reasoning
mid-session (see CLAUDE.md's delegation lesson #10, `skills/delegation.md`).

## Delegation method

`skills/delegation.md` — GOAL/SCOPE/CONSTRAINTS/VALIDATION package contract, ten
hard-won lessons (the newest: structural "what contains what" questions get
answered by reading `.pack()`/`.grid()` call sites, never by inferring from an
off-screen coordinate dump — Qwen burned a long reasoning trace doing the latter
for a question three greps would have answered outright).
