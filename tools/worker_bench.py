#!/usr/bin/env python3
"""Benchmark the local Qwen worker across reasoning settings on fixed coding tasks.

    python tools/worker_bench.py --smoke
    python tools/worker_bench.py                          # full matrix, resumable
    python tools/worker_bench.py --tasks t1 --settings think-low
    python tools/worker_bench.py --report                 # table from history only

Each pass is one small self-contained coding task with an objective verifier.
Every completed pass appends one line to analysis/worker_settings/history.jsonl
and flushes immediately, so a killed run loses nothing and a re-run skips
finished (task, setting) pairs. Model outputs are kept under out/ for review.

Sampling follows the Qwen3.8 model card: thinking T=1.0 top_p=0.95 top_k=20
presence_penalty=0; non-thinking T=0.7 top_p=0.8 top_k=20 presence_penalty=1.5.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "analysis" / "worker_settings"
HISTORY = DATA / "history.jsonl"
OUT = DATA / "out"

ENDPOINT = "http://127.0.0.1:8888/v1/chat/completions"
MODEL = "unsloth/Qwen3.8-27B-GGUF"
MAX_TOKENS = 16384
HTTP_TIMEOUT = 300

SETTINGS = {
    "think-medium": {
        "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "presence_penalty": 0.0},
        "template_kwargs": {"reasoning_effort": "medium"},
        "no_think": False,
    },
    "think-low": {
        "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "presence_penalty": 0.0},
        "template_kwargs": {"reasoning_effort": "low"},
        "no_think": False,
    },
    "think-high": {
        "sampling": {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "presence_penalty": 0.0},
        "template_kwargs": {"reasoning_effort": "high"},
        "no_think": False,
    },
    "no-think": {
        "sampling": {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5},
        "template_kwargs": None,
        "no_think": True,
    },
    # Temperature sweep: identical to think-low except temperature (0.3/0.4/0.5/0.7).
    "think-low-t03": {
        "sampling": {"temperature": 0.3, "top_p": 0.95, "top_k": 20, "presence_penalty": 0.0},
        "template_kwargs": {"reasoning_effort": "low"},
        "no_think": False,
    },
    "think-low-t04": {
        "sampling": {"temperature": 0.4, "top_p": 0.95, "top_k": 20, "presence_penalty": 0.0},
        "template_kwargs": {"reasoning_effort": "low"},
        "no_think": False,
    },
    "think-low-t05": {
        "sampling": {"temperature": 0.5, "top_p": 0.95, "top_k": 20, "presence_penalty": 0.0},
        "template_kwargs": {"reasoning_effort": "low"},
        "no_think": False,
    },
    "think-low-t07": {
        "sampling": {"temperature": 0.7, "top_p": 0.95, "top_k": 20, "presence_penalty": 0.0},
        "template_kwargs": {"reasoning_effort": "low"},
        "no_think": False,
    },
}

TASKS = {
    "t1": {
        "title": "truncate_middle (mechanical spec implementation)",
        "prompt": (
            "Write a Python function with exactly this contract. Return only the code.\n\n"
            "def truncate_middle(name: str, width: int) -> str:\n"
            '    """Shorten `name` to exactly `width` characters by cutting the middle.\n\n'
            "    - If len(name) <= width: return name unchanged.\n"
            "    - Otherwise: keep a prefix of length a and a suffix of length b, joined by a\n"
            "      single ellipsis character (U+2026, counts as one character), so that the\n"
            "      result has exactly `width` characters: a + b = width - 1.\n"
            "      On an odd split the extra character goes to the suffix:\n"
            "      a = (width - 1) // 2, b = width - 1 - a.\n"
            "    - `width` is always >= 4; behaviour for width < 4 is unspecified.\n"
            '    """\n'
        ),
        "verify": (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('m', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "f = m.truncate_middle\n"
            "\n"
            "def ref(name, width):\n"
            "    if len(name) <= width:\n"
            "        return name\n"
            "    a = (width - 1) // 2\n"
            "    b = width - 1 - a\n"
            '    return name[:a] + "\\u2026" + name[len(name) - b:]\n'
            "\n"
            "cases = [\n"
            '    ("abc", 10), ("abcdef", 6), ("report_final_v2.txt", 15),\n'
            '    ("abcdefghij", 8), ("abcdefghij", 9), ("abc", 4),\n'
            '    ("a" * 100, 20), ("x" * 37, 13), ("hello_world.py", 10),\n'
            "]\n"
            "for name, width in cases:\n"
            "    got, exp = f(name, width), ref(name, width)\n"
            "    assert got == exp, (name, width, repr(got), repr(exp))\n"
            "    if len(name) > width:\n"
            "        assert len(got) == width, (name, width, len(got))\n"
        ),
    },
    "t2": {
        "title": "cross_fields (grid arithmetic with leftover distribution)",
        "prompt": (
            "Write a Python function with exactly this contract. Return only the code.\n\n"
            "def cross_fields(window_w: int, window_h: int, border: int, gap: int) -> dict:\n"
            '    """Sizes of the four equal fields of a bordered 2x2 grid.\n\n'
            "    A `border`-pixel frame surrounds the window and a `gap`-pixel cross divides it.\n"
            "    Available width = window_w - 2*border - gap. Each column gets avail // 2, with\n"
            "    any leftover pixel going to the RIGHT column. Same for rows: each row gets\n"
            "    (window_h - 2*border - gap) // 2, leftover pixel to the BOTTOM row.\n"
            "    Return {\"tl\": (w, h), \"tr\": (w, h), \"bl\": (w, h), \"br\": (w, h)} where each\n"
            "    value is (field_width, field_height). Fields may differ by at most 1 pixel.\n"
            '    """\n'
        ),
        "verify": (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('m', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "f = m.cross_fields\n"
            "r = f(1000, 800, 20, 20)\n"
            'assert r == {"tl": (470, 370), "tr": (470, 370), "bl": (470, 370), "br": (470, 370)}\n'
            "r = f(1001, 801, 20, 20)\n"
            'assert r == {"tl": (470, 370), "tr": (471, 370), "bl": (470, 371), "br": (471, 371)}\n'
            "r = f(1280, 960, 20, 20)\n"
            'assert r == {"tl": (610, 450), "tr": (610, 450), "bl": (610, 450), "br": (610, 450)}\n'
            "r = f(1921, 1081, 20, 20)\n"
            'assert r == {"tl": (930, 510), "tr": (931, 510), "bl": (930, 511), "br": (931, 511)}\n'
        ),
    },
    "t3": {
        "title": "boundary bug fix (debugging)",
        "prompt": (
            "This function does not match its docstring. Fix it and return only the corrected code.\n\n"
            "def split_keep_refuse(values: list, limit: float) -> tuple:\n"
            '    """Split `values` into (kept, refused).\n\n'
            "    A value is kept iff its magnitude is STRICTLY LESS than `limit`; everything else\n"
            "    is refused. Order within each list is preserved.\n"
            '    """\n'
            "    kept = [v for v in values if abs(v) < limit]\n"
            "    refused = [v for v in values if abs(v) <= limit]\n"
            "    return kept, refused\n\n"
            "Failing case: split_keep_refuse([3, 5, -7], 5) must return ([3], [5, -7]) but the\n"
            "code above returns ([3, 5], [3, 5]).\n"
        ),
        "verify": (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('m', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "f = m.split_keep_refuse\n"
            "assert f([3, 5, -7], 5) == ([3], [5, -7])\n"
            "assert f([], 5) == ([], [])\n"
            "assert f([-5.0, 4.9, 100], 5) == ([4.9], [-5.0, 100])\n"
            "assert f([1, 2, 3], 10) == ([1, 2, 3], [])\n"
        ),
    },
    "t4": {
        # Real contest problem, never seen in training: AtCoder Beginner Contest 474,
        # Task B "Exit Order" (score 200), held 2026-09-06 — after Qwen3.8's release
        # (Aug 2026). Statement and samples verbatim from
        # https://atcoder.jp/contests/abc474/tasks/abc474_b
        "title": "ABC474-B exit order (novel contest problem, unseen)",
        "prompt": (
            "Problem statement (AtCoder Beginner Contest 474, Task B):\n\n"
            "There is a movie theater with N seats numbered 1 through N, and one customer is\n"
            "sitting in each seat. As a measure against congestion, this theater has customers\n"
            "leave according to the following rule.\n\n"
            "- Divide the customers into groups of 10 people each, in increasing order of the\n"
            "  seat number they are sitting in.\n"
            "- The groups leave in order, starting from the group consisting of the customers\n"
            "  with the smallest seat numbers. Customers within the same group may leave in any\n"
            "  order.\n\n"
            "Here, the last group may have fewer than 10 customers.\nThe customer who left i-th\n"
            "was the one sitting in seat P_i. Determine whether the N customers left according to\n"
            "the rule.\n\n"
            "Constraints: 10 <= N <= 100; (P_1, ..., P_N) is a permutation of (1, ..., N).\n\n"
            "Sample 1 input: N=25, P = [1,6,5,7,8,10,2,4,3,9,15,17,12,11,19,20,18,13,14,16,\n"
            "21,23,24,22,25] -> output Yes\n"
            "Sample 2 input: N=11, P = [11,10,7,2,1,5,6,4,8,9,3] -> output No\n\n"
            "Implement this as a Python function with exactly this contract. Return only the code.\n\n"
            'def exit_order(n: int, p: list) -> str:\n'
            '    """Return "Yes" if the exit order `p` (a permutation of 1..n) follows the rule,\n'
            '    otherwise "No".\n'
            '    """\n'
        ),
        "verify": (
            "import importlib.util, sys\n"
            "spec = importlib.util.spec_from_file_location('m', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(m)\n"
            "f = m.exit_order\n"
            "\n"
            "def ref(n, p):\n"
            "    g = [(x - 1) // 10 for x in p]\n"
            '    return "Yes" if all(g[i] <= g[i + 1] for i in range(n - 1)) else "No"\n'
            "\n"
            "# official AtCoder samples\n"
            "assert f(25, [1,6,5,7,8,10,2,4,3,9,15,17,12,11,19,20,18,13,14,16,21,23,24,22,25]) == \"Yes\"\n"
            "assert f(11, [11,10,7,2,1,5,6,4,8,9,3]) == \"No\"\n"
            "# edge cases derived from the statement\n"
            "assert f(10, list(range(1, 11))) == \"Yes\"          # single group, in order\n"
            "assert f(10, list(range(10, 0, -1))) == \"Yes\"     # single group, any order\n"
            "assert f(20, [5,3,8,1,9,2,7,4,6,10, 14,11,19,12,17,15,13,20,18,16]) == \"Yes\"\n"
            "assert f(20, [11] + list(range(1, 11)) + list(range(12, 21))) == \"No\"  # group 2 first\n"
            "assert f(30, list(range(1, 11)) + list(range(21, 31)) + list(range(11, 21))) == \"No\"\n"
            "big = [ i * 10 + r for i in range(10) for r in range(10, 0, -1) ]\n"
            "assert f(100, big) == \"Yes\"                       # each group reversed\n"
            "bad = big[:]; bad[5], bad[95] = bad[95], bad[5]\n"
            "assert f(100, bad) == \"No\"                        # one cross-group swap\n"
        ),
    },
    "t5": {
        # Hard open-ended debugging analysis (user-supplied adversarial scenario with
        # deliberate red herrings). Verifier is structural: corrected code must parse
        # and the answer must contain the key objective facts; the architect reads
        # the full output for the real judgement.
        "title": "CUDA grid_sample debugging (hard analysis, open-ended)",
        "timeout": 1800,
        "max_tokens": 32768,
        "prompt": (
            "DEBUGGING TEST — DO NOT GUESS\n\n"
            "You are debugging a Python image-processing service running on Windows 11.\n\n"
            "Environment:\n"
            "- Python 3.12\n- FastAPI\n- PyTorch CUDA\n- OpenCV\n- NVIDIA RTX GPU\n"
            "- The service processes images concurrently\n- CPU processing works correctly\n"
            "- GPU processing fails intermittently\n\n"
            "Reported symptom:\n\n"
            "The first 2–5 requests after starting the server usually work.\n"
            "After that, requests occasionally fail with:\n\n"
            "RuntimeError: CUDA error: invalid argument\n\n"
            "The traceback points to:\n\n"
            "    result = torch.nn.functional.grid_sample(\n"
            "        image_tensor,\n"
            "        sampling_grid,\n"
            '        mode="bilinear",\n'
            '        padding_mode="border",\n'
            "        align_corners=False\n"
            "    )\n\n"
            "However, adding:\n\n"
            "    torch.cuda.synchronize()\n\n"
            "immediately before grid_sample moves the traceback to a completely different\n"
            "operation.\n\n"
            "The developer claims:\n"
            '"grid_sample is broken in the new PyTorch version."\n\n'
            "Relevant code:\n\n"
            "--------------------------------------------------\n"
            "worker.py\n"
            "--------------------------------------------------\n\n"
            "_executor = ThreadPoolExecutor(max_workers=4)\n\n"
            "def process(image):\n"
            "    image = cv2.imread(image)\n\n"
            "    tensor = torch.from_numpy(image).cuda()\n\n"
            "    future = _executor.submit(run_gpu, tensor)\n\n"
            "    tensor = tensor.half()\n\n"
            "    return future.result()\n\n\n"
            "def run_gpu(tensor):\n"
            "    with torch.no_grad():\n"
            "        tensor = tensor.permute(2, 0, 1).unsqueeze(0)\n\n"
            "        grid = make_grid(tensor)\n\n"
            "        result = torch.nn.functional.grid_sample(\n"
            "            tensor,\n"
            "            grid,\n"
            '            mode="bilinear",\n'
            '            padding_mode="border",\n'
            "            align_corners=False\n"
            "        )\n\n"
            "        return result.cpu()\n\n\n"
            "--------------------------------------------------\n"
            "grid.py\n"
            "--------------------------------------------------\n\n"
            "_last_grid = None\n\n"
            "def make_grid(image):\n"
            "    global _last_grid\n\n"
            "    h = image.shape[-2]\n"
            "    w = image.shape[-1]\n\n"
            "    if _last_grid is not None:\n"
            "        if _last_grid.shape[-2:] == (h, w):\n"
            "            return _last_grid\n\n"
            "    y, x = torch.meshgrid(\n"
            "        torch.linspace(-1, 1, h, device=image.device),\n"
            "        torch.linspace(-1, 1, w, device=image.device),\n"
            '        indexing="ij"\n'
            "    )\n\n"
            "    grid = torch.stack((x, y), dim=-1)\n"
            "    grid = grid.unsqueeze(0)\n\n"
            "    _last_grid = grid\n\n"
            "    return grid\n\n\n"
            "--------------------------------------------------\n"
            "server.py\n"
            "--------------------------------------------------\n\n"
            '@app.post("/process")\n'
            "async def endpoint(file: UploadFile):\n\n"
            "    path = save_upload(file)\n\n"
            "    result = await asyncio.get_event_loop().run_in_executor(\n"
            "        None,\n"
            "        process,\n"
            "        path\n"
            "    )\n\n"
            "    return encode(result)\n\n\n"
            "--------------------------------------------------\n"
            "recent changes\n"
            "--------------------------------------------------\n\n"
            "1. PyTorch was upgraded from 2.8 to 2.11.\n"
            "2. CUDA driver was upgraded at approximately the same time.\n"
            "3. The service was changed from single-threaded processing to 4 workers.\n"
            "4. An optimization was added to cache the sampling grid.\n"
            "5. An optimization was added to convert tensors to FP16.\n"
            "6. Images now arrive with varying resolutions.\n"
            "7. The error disappears almost completely when only one request is sent\n"
            "   at a time.\n"
            "8. Running with CUDA_LAUNCH_BLOCKING=1 changes the reported failing line.\n"
            "9. Restarting the service always temporarily fixes the problem.\n"
            "10. GPU memory usage does NOT continuously increase.\n\n"
            "Observed behavior:\n\n"
            "Request A:\n1920 × 1080 → works\n\n"
            "Request B:\n1920 × 1080 → works\n\n"
            "Request C:\n1080 × 1920 → sometimes fails\n\n"
            "Request D:\n1024 × 1024 → sometimes works\n\n"
            "Request E:\n1920 × 1080 → sometimes fails\n\n"
            "The developer tried:\n\n"
            "- reinstalling PyTorch\n- reinstalling NVIDIA drivers\n- disabling FP16\n"
            "- adding torch.cuda.synchronize()\n- increasing GPU memory\n"
            "- reducing image size\n- restarting the service\n\n"
            "None produced a reliable fix.\n\n"
            "TASK\n\n"
            "Do NOT simply identify a likely bug.\n\n"
            "Perform a rigorous debugging analysis.\n\n"
            "1. Identify every independent race condition, lifetime problem, device/state problem,\n"
            "   shape problem, and synchronization problem you can find.\n\n"
            "2. Determine which problem is capable of directly producing:\n"
            "       CUDA error: invalid argument\n\n"
            "3. Explain why CUDA_LAUNCH_BLOCKING=1 changes the apparent traceback.\n\n"
            "4. Explain why restarting the server temporarily fixes the problem.\n\n"
            "5. Explain why changing image resolution changes the failure probability.\n\n"
            "6. Explain whether the FP16 conversion is actually relevant.\n\n"
            "7. Determine whether the cached `_last_grid` can become invalid even though its\n"
            "   shape appears correct.\n\n"
            "8. Explain whether moving tensors between threads is safe in this code.\n\n"
            "9. Identify the MINIMUM code changes required for correctness.\n"
            "   Do not propose unnecessary rewrites.\n\n"
            "10. Then provide a corrected implementation.\n\n"
            "11. Finally, design TWO additional experiments that would distinguish between:\n"
            "      A) stale cached CUDA tensor\n"
            "      B) concurrent CUDA execution/state corruption\n"
            "      C) tensor lifetime / ownership problem\n"
            "      D) an actual PyTorch 2.11 regression\n\n"
            "IMPORTANT:\n\n"
            "Several explanations above are deliberately plausible but wrong.\n\n"
            "Do not blame PyTorch merely because the error appears inside grid_sample.\n\n"
            "Do not assume that CUDA errors occur synchronously.\n\n"
            "Do not assume that equal tensor shapes imply equal tensor validity.\n\n"
            'Do not recommend "just synchronize everything" as the final solution.\n\n'
            "Your answer must explicitly rank the hypotheses by probability and explain\n"
            "what evidence would falsify each one.\n"
        ),
        "verify": (
            "import ast, json, re, sys\n"
            "raw = json.load(open(sys.argv[1].rsplit('.', 1)[0] + '.json', encoding='utf-8'))\n"
            "msg = raw['choices'][0]['message']\n"
            "text = (msg.get('content') or '') + '\\n' + (msg.get('reasoning_content') or '')\n"
            "blocks = re.findall(r'```(?:python)?\\s*\\n(.*?)```', text, re.DOTALL)\n"
            "ok_code = False\n"
            "for b in blocks:\n"
            "    try:\n"
            "        tree = ast.parse(b)\n"
            "    except SyntaxError:\n"
            "        continue\n"
            "    if any(isinstance(n, ast.FunctionDef) for n in ast.walk(tree)):\n"
            "        ok_code = True\n"
            "assert ok_code, 'no parseable python function block (corrected implementation?)'\n"
            "low = text.lower()\n"
            "assert re.search(r'half|fp16', low) and re.search(\n"
            "    r'dead|no-op|irrelevant|never used|unused|red herring|not (applied|used|effective)', low), \\\n"
            "    'FP16 dead-code point missing'\n"
            "assert re.search(r'asynchron|synchronization point|surface[sd]? (later|at)|attribut', low), \\\n"
            "    'async CUDA error attribution missing'\n"
            "assert re.search(r'\\block\\b|race|thread-safe|governed by the gil', low), \\\n"
            "    'cache race / thread-safety point missing'\n"
        ),
    },
}


def load_key() -> str:
    cfg = json.loads(Path.home().joinpath(".qwen", "settings.json").read_text(encoding="utf-8"))
    return cfg["env"]["UNSLOTH_API_KEY"]


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() + "\n" if m else text.strip() + "\n"


def completed_pairs() -> set:
    if not HISTORY.exists():
        return set()
    pairs = set()
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                row = json.loads(line)
                pairs.add((row["task"], row["setting"]))
            except (json.JSONDecodeError, KeyError):
                pass
    return pairs


def run_pass(task_id: str, setting: str, key: str) -> dict:
    task = TASKS[task_id]
    cfg = SETTINGS[setting]
    prompt = task["prompt"] + ("\n/no_think" if cfg["no_think"] else "")
    body = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": task.get("max_tokens", MAX_TOKENS),
        "stream": False,
        **cfg["sampling"],
    }
    if cfg["template_kwargs"]:
        body["chat_template_kwargs"] = cfg["template_kwargs"]
    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
    )
    t0 = time.monotonic()
    row = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "task": task_id,
           "setting": setting, "title": task["title"]}
    try:
        with urllib.request.urlopen(req, timeout=task.get("timeout", HTTP_TIMEOUT)) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        wall = time.monotonic() - t0
        msg = data["choices"][0]["message"]
        content = msg.get("content") or ""
        OUT.mkdir(parents=True, exist_ok=True)
        raw_file = OUT / f"{task_id}__{setting}.json"
        code_file = OUT / f"{task_id}__{setting}.py"
        raw_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        code_file.write_text(extract_code(content), encoding="utf-8")
        verify_file = DATA / f"verify_{task_id}.py"
        if not verify_file.exists():
            verify_file.write_text(task["verify"], encoding="utf-8")
        v = subprocess.run([sys.executable, str(verify_file), str(code_file)],
                           capture_output=True, text=True, timeout=30)
        row.update({
            "ok": v.returncode == 0,
            "finish": data["choices"][0].get("finish_reason"),
            "wall_s": round(wall, 2),
            "usage": data.get("usage"),
            "reasoning_chars": len(msg.get("reasoning_content") or ""),
            "content_chars": len(content),
            "code_file": str(code_file.relative_to(ROOT)),
            "verify_stderr": (v.stderr.strip()[-400:] if v.returncode else None),
            "error": None,
        })
    except Exception as exc:
        row.update({"ok": False, "wall_s": round(time.monotonic() - t0, 2), "usage": None,
                    "content_chars": None, "code_file": None, "verify_stderr": None,
                    "error": f"{type(exc).__name__}: {exc}"})
    with HISTORY.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
        fh.flush()
    return row


def report() -> None:
    if not HISTORY.exists():
        print("no history yet")
        return
    rows = [json.loads(l) for l in HISTORY.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_setting: dict[str, list] = {}
    for r in rows:
        by_setting.setdefault(r["setting"], []).append(r)
    print(f"{'setting':<14} {'runs':>4} {'pass':>5} {'wall_s':>8} {'compl_tok':>10} "
          f"{'prompt_tok':>10} {'reason_ch':>9} {'chars':>7}")
    for s in sorted(by_setting):
        rs = by_setting[s]
        n = len(rs)
        passed = sum(1 for r in rs if r["ok"])
        wall = sum(r["wall_s"] or 0 for r in rs) / n
        def tok(key):
            vals = [ (r["usage"] or {}).get(key) for r in rs ]
            vals = [v for v in vals if isinstance(v, (int, float))]
            return sum(vals) / len(vals) if vals else 0
        def avg(key):
            vals = [r[key] for r in rs if isinstance(r.get(key), (int, float))]
            return sum(vals) / len(vals) if vals else 0
        chars = [r["content_chars"] for r in rs if r["content_chars"] is not None]
        print(f"{s:<14} {n:>4} {passed:>3}/{n:<2} {wall:>8.1f} {tok('completion_tokens'):>10.0f} "
              f"{tok('prompt_tokens'):>10.0f} {avg('reasoning_chars'):>9.0f} "
              f"{(sum(chars)/len(chars) if chars else 0):>7.0f}")
    print("\nper-run detail:")
    for r in rows:
        u = r["usage"] or {}
        print(f"  {r['ts']} {r['task']}/{r['setting']:<13} ok={int(r['ok'])} "
              f"wall={r['wall_s']}s compl={u.get('completion_tokens')} "
              f"prompt={u.get('prompt_tokens')} reason_ch={r.get('reasoning_chars')} "
              f"err={r.get('error') or ''}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=",".join(TASKS))
    ap.add_argument("--settings", default=",".join(SETTINGS))
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.report:
        report()
        return

    key = load_key()
    if args.smoke:
        req = urllib.request.Request(
            ENDPOINT,
            data=json.dumps({"model": MODEL, "messages": [{"role": "user",
                              "content": "Reply with exactly: OK"}],
                             "max_tokens": 64, "stream": False}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        print(urllib.request.urlopen(req, timeout=120).read().decode("utf-8"))
        return

    done = set() if args.force else completed_pairs()
    tasks = [t for t in args.tasks.split(",") if t]
    settings = [s for s in args.settings.split(",") if s]
    pending = [(t, s) for t in tasks for s in settings if (t, s) not in done]
    if not pending:
        print("all requested passes already in history; use --force to re-run")
        report()
        return
    print(f"{len(pending)} pass(es) pending (skipped {len(tasks) * len(settings) - len(pending)} done)")
    for t, s in pending:
        row = run_pass(t, s, key)
        u = row.get("usage") or {}
        print(f"  {t}/{s:<13} ok={int(row['ok'])} wall={row['wall_s']}s "
              f"compl={u.get('completion_tokens')} prompt={u.get('prompt_tokens')} "
              f"{row.get('error') or ''}", flush=True)
    report()


if __name__ == "__main__":
    main()
