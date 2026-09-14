# Worker environment — deep reference

Detail that no worker package needs to see, but that took real time to work out once.
Pulled out of CLAUDE.md 2026-09-13 to keep that file small (see its Governance
paragraph and the "condense, don't delete" rule it states) — CLAUDE.md's Worker
section keeps a one-line pointer here.

## The chat template is a custom build, not stock Qwen3

`C:\Users\blasa\Desktop\chat_template.jinja`, self-identifies as
`"qwen3.8-froggeric-v22.5"`. Checked 2026-09-13, corrected an earlier CLAUDE.md claim
that strict thinking-off wasn't exposed per request — it is, two ways:

- Pass `reasoning_effort: "off"` / `"none"` in `chat_template_kwargs` — forces
  `enable_thinking` false regardless of the model's own default.
- Embed a literal tag in the system or user message text: `<|think_off|>`,
  `<|think_on|>`, `<|think_low|>`, `<|think_medium|>`, `<|think_xhigh|>`. The template
  strips these back out before they reach the model — a real, hard override, not a
  soft hint like Qwen3's own `/no_think` suffix.

`reasoning_effort` also injects an actual instruction into the system prompt: "Keep
your thinking brief and focused..." for `low`, "...validate key assumptions, consider
plausible alternatives..." for `xhigh`. **`medium` gets no injected instruction at
all** — just the model's own default behaviour, no steering text.

Template falls back to **`low`** when `reasoning_effort` is omitted entirely (edited
2026-09-13, was `medium` in the template's own default — matches CLAUDE.md's decided
default instead of silently defeating it for any caller that forgets the flag).

**Token-efficiency lever in the same template**: `preserve_thinking` /
`preserve_reasoning`. Default was `true` — every past assistant turn's `<think>` block
gets replayed back into context on every later request, compounding the
re-prefill-per-round-trip cost CLAUDE.md's Worker section already names as the main
cost driver. **Changed the template default to `false`** (2026-09-13): only the most
recent turn's thinking is kept; a caller that genuinely wants full replay can still
pass `preserve_reasoning: true` per request. Not re-benchmarked — direction (fewer
replayed tokens) is unambiguous, exact savings on real packages is not measured.

The same template auto-escalates on repeated tool failures: after 2+ consecutive
tool-call errors it injects a system warning demanding a fundamentally different
approach — a built-in nudge, not something this project's own code does.

## `reasoning_tokens` always reports 0 — confirmed llama.cpp server bug

2026-09-13. Not this project's bug, not the template's:
[ggml-org/llama.cpp#22659](https://github.com/ggml-org/llama.cpp/issues/22659),
[QwenLM/qwen-code#7236](https://github.com/QwenLM/qwen-code/issues/7236) both confirm
it. Thinking genuinely happens and `reasoning_content` comes back correct; only its
*token count* in `usage.completion_tokens_details` is never populated by llama.cpp's
server (LM Studio reports it correctly with the same models — this is backend-specific
to llama.cpp, not a property of the model or the quantization). The total
completion-token count stays trustworthy — reasoning tokens are counted inside it,
just never broken out into their own field. `opencode-token-monitor`'s "Reasoning: 0"
line inherits this gap; don't read it as "no thinking happened."

## Where token counts actually live

2026-09-13. Ground truth, not the plugin's summary of it: opencode itself logs
token usage into its own SQLite DB, `C:\Users\<user>\.local\share\opencode\opencode.db`
(confirmed live — the `-wal` file was still updating during a session). Table
`session`, columns `tokens_input` / `tokens_output` / `tokens_reasoning` /
`tokens_cache_read` / `tokens_cache_write`, one row per session, updated as it runs.
Queryable directly with any SQLite client (`sqlite3`, Python's `sqlite3` module,
DB Browser for SQLite) without going through the agent or the
`opencode-token-monitor` plugin at all — this is very likely what that plugin
reads from. Granularity is per-session only in this table; no per-message token
columns were found elsewhere in the schema. Read-only access needs a `file:...?mode=ro`
URI (or just don't write) since opencode may hold the file open while running.

## `opencode-token-monitor` crash — confirmed Windows path bug, patched locally

2026-09-13. This was the repeated "opencode crashed" reports, not Unsloth Studio and
not the model. Root cause found directly in the installed package:
`lib/history.ts` → `C:\Users\<user>\.cache\opencode\packages\opencode-token-monitor@latest\
node_modules\opencode-token-monitor\dist\plugin.js`. `getShardPath` builds the session-
history shard path with Node's `path.join` (backslash-separated on Windows);
`saveSessionRecord` (line ~486, called from the `session.idle` handler) then does
`shardPath.substring(0, shardPath.lastIndexOf("/"))` to get the containing dir — hardcoded
forward slash, finds none on Windows, `lastIndexOf` returns `-1`, `substring(0, -1)`
collapses to `""`, and `mkdirSync("", {recursive:true})` throws `ENOENT`. Fatal inside
the idle handler, took the whole session down. Windows-only; would never reproduce on
Linux/macOS, which is presumably why it shipped.

**Patched the local vendored copy** (two-line diff, not the plugin's real source — just
the cached build output): added `dirname as dirname2` to the existing `from "path"`
import, changed the broken line to `const dir = dirname2(shardPath);`. Reversible,
low-risk (local cache file, not this project). **Will silently revert** the next time
the plugin re-resolves — it's pinned `@latest` in `opencode.jsonc` (Worker section) —
so if crashes resume after an unrelated opencode/plugin update, re-check this file
before assuming a new bug.

## A real measured session, for scale

2026-09-13, `opencode-token-monitor`, session `ses_f6537355fffe7C07qpvddqtb77`, 13
messages, "build" agent: 195,353 input / 8,846 output tokens (≈22:1), cache hit rate
53.8%. Tool usage: 10× `read` + 2× `grep`, **all on the same file** (`gui.py`),
researching the ROI-ruler-drag package. Zero edits in this sample — pure exploration.
Illustrates the exploration-removal lever CLAUDE.md's Worker section already states in
the abstract: handing over exact names/line numbers instead of letting the worker
re-read a file repeatedly is the concrete lever this number argues for.

## Server / launcher notes (2026-09-13 debugging session)

- Launch via a desktop `.bat`: `unsloth start opencode --model
  unsloth/Qwen3.8-27B-GGUF:UD-IQ4_XS --reasoning-effort medium`. A double-clicked
  `.bat` runs with its own folder (Desktop) as the working directory — **not** the
  project — which silently changed behaviour when other things were also being
  debugged at the same time. Fix: `cd /d "D:\Coding\Batch-Perspective-Correction"` as
  the first real line of the script, before the `unsloth start` call.
- `opencode`'s own `--help` is the source of truth for its flags, not assumption:
  `-c`/`--continue` (resume the last session) is valid; `--dir` is **not** — the
  project path is a bare positional (`opencode [project]`), not a flag.
- In-TUI resume (not a startup arg): `/sessions` lists and switches sessions;
  `/resume` and `/continue` are aliases for the same command.
- `opencode-token-monitor` plugin installs via `opencode.jsonc`'s `"plugin":
  ["opencode-token-monitor@latest"]` array (global config:
  `~/.config/opencode/opencode.jsonc` on this machine). Registers `token_stats`/
  `token_history`/`token_export` as agent-callable tools (not confirmed as slash
  commands) — ask the agent directly ("show token usage for this session") rather than
  typing a command.
- A `forrtl: error (200): program aborting due to window-CLOSE event` in
  `unsloth-start-server-*.log` (in `%TEMP%`) is a Fortran-runtime trap from a
  still-running `llama-server` process losing its console window — usually a stale
  window from a previous debugging attempt being closed, not a fresh bug. Stop
  everything (`unsloth studio stop`), confirm no orphaned `llama-server.exe`/python
  processes remain, then start clean.
