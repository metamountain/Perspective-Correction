# QWEN.md — agent operating rules

**This file holds no status.** It used to, and by 2026-09-20 it and `CLAUDE.md`
described two different projects — the exact "two lists that must agree will
not" failure both files warn about. **Status lives in `CLAUDE.md`'s Ledger, and
nowhere else.** What is left here is the part that is genuinely about *how an
agent works in this repo*: the two coordinate rules that have been broken more
than any other thing in the project, how to verify visual work, and what the
tools are.

- **Read `CLAUDE.md` first.** It has the hard rules, how to run the tests, and
  what is open.
- **Findings go in `debug.md`.** It is 1383 lines — ask a subagent about it
  rather than reading it into context.

---

## GUI coordinate scaling (CRITICAL — never mix spaces)

The before-pane canvas has **two coordinate spaces** that must never be mixed:

| Space | Units | Used by |
|-------|-------|---------|
| **Canvas pixels** (photo-relative) | `event.x - _before_off[0]` | Click handlers, rubber bands, cursor tracking |
| **Full-resolution pixels** | Original image coordinates | `session.planar_quad`, `session.control_lines`, all stored geometry |

**The conversion is `_before_scale`** (canvas px per full-res px):

```python
# Canvas → Full-res (STORING a point):
fx = x / self._before_scale
fy = y / self._before_scale
self.session.set_planar_point(i, fx, fy)

# Full-res → Canvas (DRAWING a stored point):
cx = ox + px * self._before_scale
cy = oy + py * self._before_scale

# Hit-test (canvas point vs full-res stored points):
hit = self.session.pick_planar_corner(fx, fy, display_scale=self._before_scale)
```

**Rules:**
1. **NEVER store `event.x - offset` directly into a session array.** Always
   divide by `_before_scale` first. The session stores full-resolution pixels;
   the canvas is a scaled-down preview.
2. **NEVER draw a stored point without multiplying by `_before_scale`.** A
   full-res coordinate drawn 1:1 on a 0.4× canvas lands at 40 % of its intended
   position.
3. **Hit-tests take `display_scale`** so the grab radius is in screen pixels, not
   image pixels.
4. **Any new click-to-place tool follows the `_click_rect` pattern**: convert on
   input, convert on draw, pass scale to hit-test.
5. **Marker projection (H-Marker / control lines):** the projected marker
   position is `canvas_x = offset + full_res_x * _before_scale`. If a refit
   changes the stored full-res point, the redraw MUST re-apply `_before_scale`.
   A *correct* full-res value drawn without scale looks like the marker jumped.
   **This has been broken and re-fixed 10+ times** (2026-09-18: "projektion
   marker wurde zerstört").

**Checklist before committing ANY change touching stored geometry or its display:**
- [ ] Every `session.set_*` / array write is in full-res pixels (divided by scale)
- [ ] Every canvas draw of a stored point multiplies by `_before_scale`
- [ ] Every hit-test passes `display_scale=self._before_scale`
- [ ] After a refit, the overlay redraw uses the SAME scale chain as initial load
- [ ] Visual check: open the GUI, place a marker, confirm it stays where you clicked

Violated in the PC Rectangle implementation (2026-09-17), which put corners at
wrong positions. The fix is always the same: `x / _before_scale` before storing,
`px * _before_scale` when drawing.

## SCALING RULE (global — the preview pipeline has exactly one chain)

Every overlay, marker, ruler or guide drawn onto the `render_after` output uses
this chain. Never invent a second one:

```
original image (self.w × self.h)
    │  × s  where s = min(1, max_edge / max(w, h))
    ▼
preview space (sw × sh)          ← H_total maps FROM here
    │  H_total  (W.build → W.plan bakes in crop/fit)
    ▼
final output (ow × oh)           ← this IS the array that _fit returns
```

1. To project a point from the original image onto `render_after`: multiply by
   `s`, then apply `H_total` via `cv2.perspectiveTransform`. **Do NOT** multiply
   by any additional factor afterwards — `H_total` already lands in final-output
   pixels.
2. `_fit()` resizes the array to `max_edge`, and `W.plan` already accounts for
   it (`ow, oh` are post-fit), so the transform result is directly drawable. No
   extra `fit_s`.
3. In `_redraw`, before/after photos are fitted to their canvas boxes by
   `_to_photo()`. Original→canvas is `photo_width / self.session.w` (before) or
   `photo_width / out_w` (after). Use these for canvas-space overlays.
4. **Never** mix the two systems. A point in original pixels is converted to
   exactly one target space before drawing.

Violated ~50 times during the H-Marker session (2026-09-17). The fix is always
the same: scale by `s`, warp by `H_total`, done.

## Visual debugging — you have vision, use it

The agent can see images, and since 2026-09-20 so can the **local worker**:
`tools/worker_agent.py` exposes `view_image(path, max_edge)`, which downscales
and attaches the picture as its own user turn. Before that the harness had no
way to hand the worker a picture, so every question about a rendered frame was
answered by reasoning from source — which is how most of the scaling bugs above
survived as long as they did.

**Use this to offload the user.** They should not have to open the GUI, take a
screenshot and paste it back just so an agent can check something.

**When to look, proactively:**
- **After any GUI change** (layout, overlay, marker, crop, guide): render it,
  look at it, *then* report done.
- **When diagnosing an offset/scale bug**: capture the pane, measure pixel
  positions in the image, compare against what the code says they should be.
- **When testing correction output**: run `render_after` on a test asset, save a
  PNG, read it back — are walls vertical, horizontals level, the crop sensible?
- **When verifying theme/colour work**: screenshot the panel and confirm the INK
  palette actually landed (no OS-default black menus).

**Rules:**
1. **Verify before reporting.** If you changed something visual you must have
   seen the result. "The code looks correct" is not sufficient for GUI work.
2. **Don't ask the user to screenshot** unless the bug needs a live drag gesture.
   Static states reproduce headlessly.
3. **Save test screenshots to `.qwen/tmp/`** so they never reach the repo.
4. Prefer the **offscreen render** (build on a hidden root, call `_redraw`, grab
   the canvas) over a full-screen grab — deterministic, and independent of where
   the window happens to sit.

## Write permissions (strict)

| File | Permission |
|---|---|
| `debug.md` | **WRITE** — findings list + proposals |
| `QWEN.md` | **WRITE** — these operating rules |
| `CLAUDE.md` | **WRITE** — the Ledger, after a change lands |
| everything else | **READ-ONLY** unless the user explicitly asks for a change |

## What a finding is

A finding names **file + symbol/line**, states the problem, and suggests a fix.
Severity: **HIGH** = likely wrong behaviour, **MED** = latent risk, **LOW** =
style/clarity.

**A finding is a pointer, not a verdict.** Measured 2026-09-15: of six findings
that came back with citations, severities and copy-paste patches, **five were
wrong**, and both HIGHs would have broken working code. Severity in a report is
the reporter's confidence, not the defect's. Grep the names, run the arithmetic,
*then* read the patch — the wrong ones pass the suite too.

## External tools / MCP servers

Available via `tool_search` (`select:<name>` or a keyword query):

| Server | Key tools | Use for |
|--------|-----------|---------|
| **context7** | `resolve-library-id`, `query-docs` | Current library docs (OpenCV, Tkinter, numpy, torch). Prefer over training data for API syntax. |
| **firecrawl** | `firecrawl_search`, `firecrawl_scrape`, `firecrawl_developer_search` | Web research when `web_fetch` gets 403/404. `developer_search` finds repos, issues, PRs. |
| **github** | `search_code`, `search_issues`, `get_file_contents`, `list_commits` | Read files from remote repos, check upstream for bug reports. |
| **playwright** | `browser_navigate`, `browser_snapshot`, `browser_take_screenshot` | Browser automation; verify rendered output. |
| **tavily** | `tavily_search`, `tavily_extract`, `tavily_research` | Structured web search; `research` for multi-source dives. |

**Which one:**
- "How do I call `cv2.ximgproc.fldLineDetector`?" → **context7**
- "Known bug in OpenCV 4.10 LSD on Windows?" → **firecrawl** `developer_search` / **github** `search_issues`
- "What does the reference implementation do for X?" → **github** `get_file_contents`
- "Find recent papers on single-image perspective correction" → **tavily_research**
