# Delegation skills — Batch Perspective Correction

How the MASTER writes a work package, and how to prompt the WORKER (Qwen3).
CLAUDE.md defines the split; this file is the operational detail, and the
evidence for it is a real session in which the worker was given three packages
and pushed back on all three — correctly, every time.

Packages live in CLAUDE.md under "Work packages". This is how to write one.

## The package contract

Every delegation carries four headings, and a package missing one will come
back wrong in a predictable way:

| heading | omit it and you get |
|---|---|
| **GOAL** — one concrete objective | a redesign |
| **SCOPE** — exact files and functions | edits in files you were protecting |
| **CONSTRAINTS** — what must not change | a "fix" that breaks a documented rule |
| **VALIDATION** — the command that proves it | "done", unverified |

## What this project learned the hard way

**1. Check the package for self-contradiction before sending it.** A package
once said *"the canvas must gain materially"* and also *"the default appearance
must not change"*, where the only source of height was the default appearance.
The worker answered: "VALIDATION step 3 is not satisfiable as written", and
reported the honest number (canvas unchanged by default, +78 % only once
collapsed). **A less careful worker returns a success that is not one.** If two
constraints can conflict, say which one wins.

**2. Quote a baseline with the probe that produced it.** A package quoted
"beat 411 px" from a window *simulated* at 2560x1440 against a figure measured
on a *maximized* one at 2560x1351 — the taskbar is 89 px, so they were two
different windows. The worker caught it. Numbers from different harnesses are
not comparable, and the flattering one is the one to re-check.

**3. Give the reason behind a constraint, not only the constraint.** Told to
wrap "rows 4-8", the worker wrapped 4-7 and excluded Save/Keep/Close on its own
reasoning: a review queue advances on Save, so a hideable Save is a *behaviour*
change, not a layout change. It was right, and it was only able to be right
because it knew what the rows were for.

**4. State the root cause as a hypothesis, not a fact.** A package named pack
order as the reason the Save row fell off a 1080p window. The real cause was
two things: pack order *and* `tk.Canvas`'s default 378x265 size request, which
the packer honoured ahead of the buttons. Fixing only the named half would have
half-worked. Say "I believe X; report if it is not the whole story".

**5. Name what must not be touched, file by file.** `review.py` must stay
Tkinter-free, `layout.py` owns pixel arithmetic, CLAUDE.md is the MASTER's.
Listing them costs one line and prevents the diff that has to be unpicked.

**6. Require a report of what it could *not* do.** The most valuable output of
two packages was the part outside them: a `_redraw` that reschedules itself
every 120 ms with no exit condition, and a 90 px preview at 1080p. Both were
real, both were verified, and neither was asked for.

**7. Continue an agent, do not respawn it.** Resuming the same worker with its
context intact took a second package from 18 tool calls to 5. A fresh start
re-reads the project and re-derives what it already knew.

**8. Verify the claims, not the prose.** Before acting on "the Save row is
unmapped at 1080p", re-probe it. Two independent checks cost a minute each and
both held; that is the level CLAUDE.md means by "review each worker result
before assigning the next task". It is not a line-by-line audit of the diff.

**9. Size the package to the *deployment's* context, not the model card's.**
The card says 32,768 native (131,072 with YaRN); the worker here actually runs
**~140k**. Ask, do not assume — the figure decides whether a package can carry
its own background or has to point at files instead. Getting it wrong in the
pessimistic direction is not free either: it produced a justification in
CLAUDE.md ("a worker cannot hold this file") that was simply untrue.

**10. A structural question gets answered by reading the code, not by inferring
it from rendered coordinates.** Asked to confirm which panel a set of controls
actually lived in, the worker dumped an off-screen widget tree's rootx/rooty and
tried to reverse-engineer the grouping from raw pixel positions — and
conflated two unrelated widget subtrees that happened to render near each
other on screen, circling for a long reasoning trace before landing anywhere.
The same question was three `grep`s on the `.pack()`/`.grid()` call sites away:
unambiguous, seconds, no inference. A coordinate dump is the right tool for
what code cannot state directly — does this actually clip, what is the real
pixel width — never for "what contains what," which the source already
answers. If a package asks "verify the current layout," say *how*: point at
the construction code first, and reserve the off-screen dump for the geometry
question it's actually for.

## Prompting Qwen3 specifically

Sampling, from the model card. **The mode matters more than the numbers:**

| | temperature | top_p | top_k | min_p |
|---|---|---|---|---|
| thinking | 0.6 | 0.95 | 20 | 0 |
| non-thinking | 0.7 | 0.8 | 20 | 0 |

* **Never use greedy decoding (temperature 0).** It is the tempting setting for
  a code worker that should be deterministic, and on Qwen3 it degrades output
  and produces endless repetition. This is the single most common
  misconfiguration.
* **Repetition:** raise `presence_penalty` toward 1.5. Above 0 it can cause
  language mixing and a slight quality loss, so treat it as a remedy, not a
  default.
* **Output length:** allow 32,768 tokens; 38,912 for genuinely hard problems. A
  truncated reasoning block reads exactly like a wrong answer.
* **Mode switching:** append `/think` or `/no_think` to a message to steer
  per-turn, when the template has `enable_thinking=True`. Use `/no_think` for
  mechanical edits (a rename, a constant change) and thinking for anything
  where the diagnosis is the work.
* **Multi-turn:** never feed the `<think>...</think>` block back as history —
  keep only final answers. Leaving it in wastes the window and degrades the
  next turn.
* **Format:** ask for the output shape explicitly. Qwen3's card recommends
  standardising it in the prompt; here that means "reply with the diff hunks,
  the validation output, and what you could not do" rather than hoping.

**Confirm the variant before trusting a size-specific claim.** CLAUDE.md names
the worker "Qwen3.8-27B"; the published Qwen3 line is 0.6B/1.7B/4B/8B/14B/32B
dense plus 30B-A3B and 235B-A22B MoE, with no 27B, so the label is probably a
local alias. The sampling table above is family-wide and holds regardless; only
context length and speed depend on which one is actually loaded.

## Run it locally -- that is the point

`qwen -m "unsloth/Qwen3.8-27B-GGUF" -p "<package>"`. A metered subagent costs
100k-140k tokens per package (measured: 108k, 126k, 137k); the local 27B costs
wall-clock. Default to local; escalate only when local has already failed.

Two traps, both seen: the CLI may be configured for a model that is **not
loaded** (ask `/v1/models` which one has `loaded: true` before blaming the CLI),
and a small `max_tokens` returns an **empty string** because the thinking block
consumed it -- which looks exactly like a broken endpoint. Measured context on
this box: **94 848** tokens.

## What not to delegate

Architecture, interfaces, cross-module debugging, and any decision this project
has already measured (see CLAUDE.md's killed ideas). The worker executes
faithfully inside a spec — which means **the outcome is gated by the quality of
the package, not by the worker.** Three of the four things that went wrong in
the session behind this file were defects in the package, not in the work.
