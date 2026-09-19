# analysis/ — local-only diagnostic scratch

Nothing here is committed except this file. `.gitignore` carries `analysis/*`
plus `!analysis/README.md` for exactly that reason: a fresh clone gets the
directory and an explanation, never anyone else's run output.

What writes here:

- `tools/shoot.py` → `analysis/shots/` (rendered before/after frames)
- `tools/worker_bench.py` → `analysis/worker_settings/history.jsonl`
- any one-off measurement script; put it in `analysis/scratch/`, not at the
  repo root, where `/_*.py` is ignored only as a safety net.

The directory was swept into `Trashcan/analysis_old` by the 2026-09-19
dead-code pass and recreated on 2026-09-20, because the tools above still
target it and `.gitignore` still describes it. Old outputs are still in
`Trashcan/analysis_old` if a past measurement needs checking.
