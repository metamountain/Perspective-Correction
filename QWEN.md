# Perspective Correction — Agent Instructions

## Project Overview

Straightens converging verticals (roll, pitch) in architectural photographs, batch or interactively. Core model: `H = K R K⁻¹` — a camera rotation with three DOF; the horizon is derived from the vertical vanishing point (`K⁻ᵀ u`), never detected separately.

**Product direction (2026-09-13 user directive): manual review is the product; unattended batch is not.** Defaults were designed for fire-and-forget safety (refuse rather than trim, multiplicative confidence). With a person reviewing each image those asymmetries no longer hold — treat caps/gates as open questions, not settled ones.

## Repository Layout

```
src/pc/           source package (setuptools, src layout)
  cli.py          argparse front end, batch orchestration
  pipeline.py     process() / analyse() — one image end-to-end
  geometry.py     camera math (K, R, H), vanishing-point fit
  lines.py        LSD line detection + classification
  mlsd.py         M-LSD detector (TFLite via ai-edge-litert)
  deeplsd.py      DeepLSD detector (torch)
  model.py        confidence scoring, decision logic
  warp.py         perspective warp + crop
  inpaint.py      fill backends (telea default, lama, comfyui)
  masks.py        region masks (BiRefNet, file-based)
  birefnet.py     BiRefNet segmentation wrapper
  layout.py       window/pane size arithmetic — pure functions, no Tk
  review.py       review state machine — pure functions, no Tk
  gui.py          Tkinter shell only (batch window + review window)
  config.py       Settings dataclass
  imageio.py      read/write, EXIF focal length
  planar.py       planar homography for corner views
  optimize.py     least-squares refinement
  prefs.py        --remember / --forget persistent defaults
  deps.py         optional-backend detection (--doctor)
  scheme.py       crop schemes (auto/aspect/inside/none)
  preview.py      debug overlay rendering
tests/            standalone test runner + modules
tools/            debug_ui.py, DeepLSD checkout lives here
models/           vendored M-LSD weights; deeplsd_md.tar (98 MB)
docs/             accuracy measurements, prior art, detector comparisons
workflows/        ComfyUI API workflows for --fill comfyui
skills/           agent skill definitions
```

## Building and Running

### Install

```bash
pip install -r requirements.txt       # run from this folder without installing
pip install -e .                      # install; gives the `pc` command
```

Python 3.9+. Core deps: numpy, opencv-python(-headless), Pillow, piexif.

### Optional extras (all lazy-imported, report missing rather than crash)

```bash
pip install -e ".[gui]"       # tkinterdnd2 for drag-and-drop
pip install -e ".[mlsd]"      # ai-edge-litert; model vendored in models/
pip install -e ".[deeplsd]"   # torch + pytlsd; also needs tools/DeepLSD checkout + models/deeplsd_md.tar
```

**No `lama` extra by design.** `simple-lama-inpainting` must be installed with `--no-deps`:

```bash
pip install --no-deps simple-lama-inpainting
```

Its stale pins downgrade Pillow/numpy and break OpenCV in the same interpreter. A test enforces that no `lama` or `ultralytics` extra appears in pyproject.toml.

### Run

```bash
python rectify.py "D:\Fotos"                  # write *_corr.jpg beside originals
python rectify.py "D:\Fotos" -o "D:\Out" -r   # to another folder, recursive
python rectify.py --gui                       # Tkinter batch window
pc --doctor                                  # what this interpreter can actually run
```

Windows shortcuts: `Perspective Correction.bat` (GUI), `run_and_log.bat` (batch + log + report.json).

### Tests

```bash
python tests/run_tests.py              # all modules, parallel worker processes (~103 s)
python tests/run_tests.py test_gui     # single module by name substring
python tests/run_tests.py -v           # show every test name
python tests/run_tests.py -s           # sequential (debugging)
```

**No pytest.** The runner is `tests/run_tests.py`; it discovers `test_*` functions in each listed module. A new `test_*.py` file **must** be added to the `MODULES` list in `run_tests.py` or `_unlisted()` will fail the run.

GUI off-screen test pattern:
```python
app = App(start_maximized=False)
app.geometry("1200x800-4000+0")  # move off-screen
app.update(); time.sleep(0.02)
# ... assertions ...
app.destroy()
```
`invalid command name ..._pump` on teardown is harmless Tk noise, not a failure.

## Development Conventions

### Architecture rules (the ones that bite)

- **No pixel arithmetic in `gui.py`.** All size/position decisions live in `layout.py` (pure functions, tested at 1366×768 through 3840×2160). `review.py` has no Tkinter import — pure state functions tested headlessly. `gui.py` is only the shell.
- **Optional backends are lazy.** Every optional import (torch, ai-edge-litert, tkinterdnd2, simple_lama) happens inside the function that needs it, with a clear "not installed" message rather than an ImportError at module load.
- **Seeded RNG everywhere.** A batch that differs on re-run is unusable.
- **Confidence is multiplicative** — any single factor can veto.
- **Beyond the limit means refuse, not trim** (unless `--clamp-beyond-limit`).

### Coding style

- Python 3.9+ compatible (`from __future__ import annotations` where needed).
- Type hints on public functions; internal helpers may omit.
- Docstrings: module-level one-liner or short paragraph explaining *what* and *why*; function docstrings only when the "why" is non-obvious.
- No comments narrating what code does. Comments explain constraints, workarounds, or invariants.
- Keep functions focused; `pipeline.py` orchestrates, submodules do one thing.

### Testing conventions

- Each module has a matching `tests/test_<module>.py`.
- Tests are plain functions named `test_*`, no classes, no fixtures, no pytest.
- Raise `SkipTest("reason")` (a builtin injected by the runner) to skip.
- Synthetic images via `tests/synth.py` helpers; real assets in `tests/assets/`.
- GUI tests use the off-screen pattern above; must not require a visible display.

### Governance and documentation

- **CLAUDE.md** is the live governance document with a Ledger tracking all work items (open/done). After every completed feature or fix, update CLAUDE.md — a change that lands without its note there is not done.
- **knowledge.md** holds analysis and external sources for research goals.
- Status lives in exactly one place: the Ledger in CLAUDE.md. Never restate status in a second section.

### Environment split (Windows, this machine)

| Interpreter | torch | transformers | tkinter | Use for |
|---|---|---|---|---|
| system python.org 3.12 | yes (CUDA) | absent | yes | GUI, CLI, M-LSD |
| ComfyUI `python_embeded` | yes | yes | **no** | BiRefNet masking (`--mask-export`) |

BiRefNet needs torch + transformers; the GUI interpreter lacks transformers, so `--mask birefnet` runs from the ComfyUI interpreter via `--mask-export`, writing PNGs consumed later through `--mask file`.

### Off-limits

- `D:\Batch-Perspective-Correction` (a second, older copy) is off-limits per user directive — do not write, sync, or run tools there.
- Do not add `lama` or `ultralytics` to pyproject.toml extras.
