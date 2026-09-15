"""Drive the local Qwen worker as a real agent, over the OpenAI tool-call API.

Why this file exists: the worker was written off for weeks as "reads but cannot
write or execute".  That was never the model -- it was `qwen -p`, a client that
registers no tools.  Talk to llama.cpp under Unsloth Studio directly with a
`tools[]` array and it emits `finish_reason=tool_calls` in ~1.5 s and drives a
multi-step job to completion.  See the Worker section of CLAUDE.md.

Usage:
    python tools/worker_agent.py package.txt      # package on disk
    python tools/worker_agent.py -                # package on stdin

The tools below are deliberately narrow and repo-scoped: the worker may read,
search, replace one unique snippet, and run the suite.  It gets no raw shell.
The architect still reviews `git diff` before anything is committed -- a green
suite is evidence, not permission.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

# Windows hands this process a cp1252 stdout, and the worker writes arrows and
# dashes like any model will.  Printing its own answer then killed the run after
# the work was already done and committed to disk -- the harness losing the
# report, not the worker failing.  Re-open stdout as UTF-8 and never crash on
# an unencodable character again.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO = pathlib.Path(__file__).resolve().parent.parent
BASE = os.environ.get("UNSLOTH_STUDIO_URL", "http://127.0.0.1:8888").rstrip("/") + "/v1"
MODEL = os.environ.get("UNSLOTH_MODEL", "unsloth/Qwen3.8-27B-GGUF")
# 16 was enough for an edit package and not for a read-only audit: a sweep
# across every widget in gui.py ran out mid-search and returned nothing at
# all, having done the reading.  A turn is cheap here -- the model is local.
MAX_TURNS = int(os.environ.get("WORKER_MAX_TURNS", "40"))

# `invalid command name ..._pump` is Tk teardown noise, documented in CLAUDE.md
# under "Running things".  It is NOT a failure, and a harness that hands it back
# as the result of a test run makes the worker retry forever -- which cost eight
# wasted turns the first time this was used.
_TK_NOISE = re.compile(r"invalid command name \"\d+_pump\"")
_SUMMARY = re.compile(r"^\d+ test\(s\), .*", re.M)


def _safe(rel: str) -> pathlib.Path:
    """Resolve *rel* inside the repo, refusing anything that escapes it."""
    p = (REPO / rel).resolve()
    if p != REPO and REPO not in p.parents:
        raise ValueError(f"path escapes the repo: {rel}")
    return p


def read_file(path: str, start: int = 1, end: int = 0) -> str:
    lines = _safe(path).read_text(encoding="utf-8").splitlines()
    end = end or len(lines)
    return "\n".join(f"{i}: {l}" for i, l in enumerate(lines[start - 1:end], start))


def grep(pattern: str, files: str = "*.py") -> str:
    """Search tracked files. `files` is a git pathspec; "*" searches everything.

    It was hardwired to *.py, which is right for a code change and useless for
    an investigation: logs, skill notes and configs are where the evidence of a
    failing backend lives, and the worker could not reach any of them.
    """
    cmd = ["git", "grep", "-nE", "-e", pattern]
    if files and files != "*":
        cmd += ["--", files]
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    return (r.stdout or "(no matches)")[:4000]


def str_replace(path: str, old: str, new: str) -> str:
    p = _safe(path)
    s = p.read_text(encoding="utf-8")
    n = s.count(old)
    if n == 0:
        return "ERROR: that text is not in the file verbatim. read_file it again."
    if n > 1:
        return f"ERROR: that text appears {n} times. Include more context to make it unique."
    p.write_text(s.replace(old, new), encoding="utf-8")
    return f"OK: replaced 1 occurrence in {path}"


def run_tests(full: bool = False) -> str:
    """Run the suite and return its verdict line, not its tail."""
    cmd = [sys.executable, "tests/run_tests.py"] + (["--full"] if full else [])
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=900)
    out = _TK_NOISE.sub("", r.stdout + r.stderr)
    summary = _SUMMARY.findall(out)
    fails = [l for l in out.splitlines() if l.startswith(("FAIL", "AssertionError"))]
    if not summary:
        return "ERROR: the runner produced no summary line. Output tail:\n" + out[-800:]
    return summary[-1] + ("\n" + "\n".join(fails[-6:]) if fails else "")


_TOOLS = [
    ("read_file", "Read a repo file with line numbers.",
     {"path": {"type": "string"}, "start": {"type": "integer"}, "end": {"type": "integer"}}, ["path"]),
    ("grep", "Search tracked files (extended regex: a|b works). `files` is a git "
              "pathspec, default '*.py'; pass '*' to search logs and docs too.",
     {"pattern": {"type": "string"}, "files": {"type": "string"}}, ["pattern"]),
    ("str_replace", "Replace one unique verbatim snippet in a file.",
     {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
     ["path", "old", "new"]),
    ("run_tests", "Run the suite. full=true runs every module (~55 s).",
     {"full": {"type": "boolean"}}, []),
]
SPEC = [{"type": "function",
         "function": {"name": n, "description": d,
                      "parameters": {"type": "object", "properties": p, "required": r}}}
        for n, d, p, r in _TOOLS]
IMPL = {"read_file": read_file, "grep": grep,
        "str_replace": str_replace, "run_tests": run_tests}


def _key() -> str | None:
    """The Studio API key, read at call time -- never copied into a script."""
    try:
        s = json.load(open(os.path.expanduser("~/.qwen/settings.json"), encoding="utf-8"))
        return (s.get("env") or {}).get("UNSLOTH_API_KEY")
    except Exception:
        return None


class _Overflow(Exception):
    """The server refused the conversation -- on this llama.cpp build, too long."""


def _call(messages: list, tools: bool = True) -> dict:
    # Non-thinking sampling per the Qwen3.8 card; tool calling is only measured
    # on this path.  Thinking-mode tool calling is untested -- see CLAUDE.md.
    body = {"model": MODEL, "messages": messages,
            "temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5,
            "max_tokens": 8192, "chat_template_kwargs": {"enable_thinking": False}}
    if tools:
        body.update(tools=SPEC, tool_choice="auto")
    key = _key()
    req = urllib.request.Request(
        BASE + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 **({"Authorization": "Bearer " + key} if key else {})})
    try:
        return json.load(urllib.request.urlopen(req, timeout=900))
    except urllib.error.HTTPError as e:
        # 400 here is the context window, not a malformed request: every field
        # above is the one that worked on turn 1.  Three audit runs died this
        # way with the reading already done and nothing written down.
        if e.code == 400:
            raise _Overflow(f"{len(json.dumps(messages))} chars of conversation") from e
        raise


def _finish(msgs: list, why: str) -> str:
    """One last turn with no tools on the table: say what you found.

    A run that dies of a full context has usually done the reading -- what it
    has not done is write the report.  Elide the older tool results to make
    room, take the tools away so the only move left is prose, and ask.  A
    partial report beats a trace; if even this fails the caller returns nothing
    and the driver marks the module NOT cleared.
    """
    idx = [i for i, m in enumerate(msgs) if m.get("role") == "tool"]
    for i in idx[:max(0, len(idx) - 3)]:
        msgs[i] = {**msgs[i], "content": "(elided to make room for your answer)"}
    msgs.append({"role": "user", "content":
                 "Stop searching -- " + why + ".  Report your findings NOW, from "
                 "what you have already read, in the format the package asked "
                 "for.  If you found nothing, say that in one line."})
    try:
        final = (_call(msgs, tools=False)["choices"][0]["message"]
                 .get("content") or "").strip()
    except Exception as e:
        print(f"\n=== forced answer failed: {type(e).__name__}: {e} ===")
        return ""
    if not final:
        print("\n=== forced answer came back empty ===")
        return ""
    print(f"\n=== worker finished: forced answer ({why}) ===")
    print(final)
    return final


def run(package: str) -> str:
    msgs = [{"role": "user", "content": package}]
    seen = set()
    t0 = time.time()
    for turn in range(MAX_TURNS):
        try:
            m = _call(msgs)["choices"][0]["message"]
        except _Overflow as e:
            return _finish(msgs, f"the context is full ({e})")
        calls = m.get("tool_calls")
        msgs.append({"role": "assistant", "content": m.get("content") or "",
                     **({"tool_calls": calls} if calls else {})})
        if not calls:
            final = (m.get("content") or "").strip()
            print(f"\n=== worker finished: {turn} tool turns, {time.time() - t0:.1f}s ===")
            print(final)
            return final
        for c in calls:
            name = c["function"]["name"]
            raw = c["function"]["arguments"] or "{}"
            # The same grep three times over is what fills the window: one audit
            # asked for `def (available|describe)` in birefnet.py on turns 15,
            # 17 and 31 and got the same 900 characters back each time.  Reads
            # are pure, so the second answer can be four words long.
            if name in ("read_file", "grep") and (name, raw) in seen:
                print(f"[{turn}] {name}({raw[:100]}) -> repeat, not re-run")
                msgs.append({"role": "tool", "tool_call_id": c["id"],
                             "content": "(identical call already answered above)"})
                continue
            seen.add((name, raw))
            try:
                args = json.loads(raw)
                out = str(IMPL[name](**args))
            except Exception as e:                       # a bad call is data, not a crash
                args, out = {}, f"ERROR: {type(e).__name__}: {e}"
            head = (out.splitlines() or [""])[0][:120]
            print(f"[{turn}] {name}({json.dumps(args)[:100]}) -> {head}")
            msgs.append({"role": "tool", "tool_call_id": c["id"], "content": out[:6000]})
    return _finish(msgs, f"the turn budget ({MAX_TURNS}) is spent")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    text = sys.stdin.read() if sys.argv[1] == "-" else open(sys.argv[1], encoding="utf-8").read()
    run(text)
