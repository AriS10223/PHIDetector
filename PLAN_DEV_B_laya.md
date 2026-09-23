# Dev B — Laya local decision server (stretch goal)

Read `PLAN (1).md` first for the shared goal/scope/contract/timeline. This file is everything you need to build your track without touching Dev A's files.

**Goal:** a small local HTTP server that uses the `laya` package to flag the Safe Harbor categories regex structurally can't catch — names, addresses, individual-tied dates, and the free-form identifier categories (medical record #, health plan #, license #, device/serial #, catch-all). It must run **entirely on-device**, bound to `127.0.0.1` — never a hosted API. Sending PHI-candidate text to a third party to check whether it's PHI defeats the whole point of this tool.

You own: `server.py`, `test_server.py`. Don't edit `pii_check.py` or `test_hook.py` — that's Dev A's.

**Contract you implement:** `POST /check` with `{"text": str}` → `{"blocked": bool, "category": str, "phi_prob": float}`; `GET /health` → `{"status": "ok"}`. Bind `127.0.0.1:8420`.

## Phase 1 — Setup (do this first, it's the long pole tonight)

1. `pip install fastapi uvicorn` (laya itself is already installed — v0.3.11).
2. Prefetch the checkpoint and smoke-test the API, in the background, immediately — `pip install laya` does **not** download the model, that only happens on first `laya.load(...)`, and it's an ~800MB download plus torch/transformers already pulled in.

   Git Bash:
   ```bash
   USE_TF=0 python -c "import laya; a=laya.load('convaiinnovations/laya', device='cpu'); print(a.predict('hello there', {'q':{'type':'noul','instructions':'Is this a greeting?'}}))"
   ```
   PowerShell:
   ```powershell
   $env:USE_TF='0'; python -c "import laya; a=laya.load('convaiinnovations/laya', device='cpu'); print(a.predict('hello there', {'q':{'type':'noul','instructions':'Is this a greeting?'}}))"
   ```
   **Known gotcha:** if `laya.load()` hangs, it's `transformers` deadlocking on a TensorFlow probe at import — `USE_TF=0` must be set *before* `import laya`, not after.

   **Known gotcha (Windows): `OSError: [WinError 1314] A required privilege is not held by the client`.** Hugging Face's cache places files with symlinks, which Windows only allows with Developer Mode on or as admin. The failure is late and misleading: it happened on the demo machine after the ~800MB weights had already downloaded, when linking one small file (`tokenizer/tokenizer_config.json`) into place. Don't delete the cache and re-download.
   - **Prevent it:** before the first `laya.load()`, turn on Developer Mode (Settings → System → For developers → Developer Mode).
   - **Or repair it after the error:** the missing file is already in the cache's `blobs/` folder. It just wasn't linked. This copies every missing file into place from its blob (stdlib only, no re-download):
     ```bash
     python -c "
     import json, os, shutil, glob
     d = os.path.expanduser('~/.cache/huggingface/hub/models--convaiinnovations--laya')
     for tree in glob.glob(d + '/trees/*.json'):
         rev = os.path.basename(tree)[:-5]
         for path, meta in json.load(open(tree))['files'].items():
             dst = os.path.join(d, 'snapshots', rev, path)
             src = os.path.join(d, 'blobs', meta['blob_id'])
             if not os.path.exists(dst) and os.path.exists(src):
                 os.makedirs(os.path.dirname(dst), exist_ok=True)
                 shutil.copyfile(src, dst)
                 print('repaired', path)
     "
     ```
     Then re-run the smoke test above. On the demo machine it loaded and answered in ~7s.
   - You'll also see a `checkpoint ships invalid temperatures ... choice:11+` warning on load. It only affects choice questions with 11+ options; ours has 5, so ignore it.

3. While that runs, write `server.py` below.

## Phase 3 (numbering matches the original plan) — build `server.py`

```python
import os
os.environ.setdefault("USE_TF", "0")

import torch
torch.set_num_threads(8)  # tune; don't exceed physical core count (24 on this machine)

import laya
from fastapi import FastAPI

app = FastAPI()
agent = laya.load("convaiinnovations/laya", device="cpu")  # loads once at startup, not per-request

QUESTIONS = {
    "safe_harbor_category": {
        "type": "choice",
        "instructions": "Which HIPAA Safe Harbor identifier category, if any, does `prompt` contain? Pick 'none' if there is no identifying information.",
        "criteria": {
            "name": "A person's name",
            "geographic": "A street address, city, county, or ZIP/postal code tied to a specific person",
            "date": "A date tied to a specific individual: birth, admission, discharge, or death date, or an age over 89",
            "id_number": "A medical record number, health plan number, license number, or device/serial number",
            "none": "No identifying information present",
        },
    }
}

THRESHOLD = 0.6  # tune in Phase after-checkpoint so every negative test case passes

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
MAX_CHUNKS = 8

def chunk_text(text: str):
    chunks = []
    start = 0
    while start < len(text) and len(chunks) < MAX_CHUNKS:
        chunks.append(text[start:start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks or [""]

# warm-up: pay first-call latency at startup, not on the first real request
agent.predict("hello", QUESTIONS)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/check")
def check(body: dict):
    text = body.get("text", "")
    chunks = chunk_text(text)
    states = [{"prompt": c} for c in chunks]
    results = agent.predict_batch(states, QUESTIONS)

    best_prob = 0.0
    best_category = "none"
    for r in results:
        ans = r["answers"]["safe_harbor_category"]
        probs = ans["probabilities"]
        phi_prob = 1.0 - probs.get("none", 0.0)
        if phi_prob > best_prob:
            best_prob = phi_prob
            best_category = ans["choice"] if ans["choice"] != "none" else best_category

    return {
        "blocked": best_prob >= THRESHOLD,
        "category": best_category,
        "phi_prob": round(best_prob, 4),
    }
```

Notes on why this differs from a naive version (full detail in `PLAN (1).md`'s machine notes):
- `USE_TF=0` set in-process before `import laya`, so the server works regardless of which shell starts it.
- `torch.set_num_threads(8)` — this machine has 24 physical / 32 logical cores and CPU-only torch; tune this number, don't leave it unset, and don't exceed the physical core count.
- Chunking: Laya's agent has roughly a 512-token window with the question head capped at 192 tokens, and a plain string state gets right-truncated — a long prompt would have everything past ~1k characters silently ignored. Splitting into overlapping 800-char chunks (max 8) and taking the highest-scoring chunk avoids that.
- State passed as `{"prompt": chunk}` (matching Laya's own preset convention in `presets.py`), not a bare string — try a bare string instead if this doesn't score well in your testing.
- Threshold on `1 - P(none)`, not "top choice is not none" — the base checkpoint wasn't fine-tuned for this exact task, so raw argmax is too trigger-happy on ordinary text. Tune `THRESHOLD` against real test cases below.
- Warm-up call at import time, not on first request — keeps first-request latency out of the demo.
- Explicitly **not** using the bundled `laya.serve` module — it defaults to binding `0.0.0.0` (LAN-exposed) and preloading every checkpoint, neither of which you want for a small local PHI-scanning sidecar.
- If the warm-up call raises `options exceed head_max_len=192`, one of the criteria strings is too long — trim it (the `id_number` line is the longest, tighten it first).

Run it:
```
python -m uvicorn server:app --host 127.0.0.1 --port 8420
```
(`python -m uvicorn`, not a bare `uvicorn` command — guarantees the interpreter that actually has `laya` installed, independent of whether `Scripts\` is on PATH.)

Verify manually while it's up:
```bash
curl http://127.0.0.1:8420/health
curl -X POST http://127.0.0.1:8420/check -H "Content-Type: application/json" -d "{\"text\": \"send this to Sarah Chen\"}"
```

## 🛑 CHECKPOINT — STOP HERE
Report in the shared chat/channel:
- Server up and `/health` responding
- Warm latency for a 1-chunk request and an 8-chunk (worst-case) request

Wait for the team to say go before Dev A wires this into the hook. If told to skip Laya for the demo, spend the remaining time helping Dev A harden `test_hook.py` edge cases and demo prep.

## After go — tune and test

Create `test_server.py`: POST each case to `/check` and print `category` / `phi_prob` / `blocked`.

```python
import json, urllib.request

POSITIVES = [
    "send this to Sarah Chen",
    "she was born March 3rd 1961",
    "lives at 42 Elm Street, Springfield",
    "MRN 00482913",
]
NEGATIVES = [
    "fix the off-by-one in getUserName",
    "schedule the deploy for Friday",
    "add a zip code field to the form",
]

def check(text):
    req = urllib.request.Request(
        "http://127.0.0.1:8420/check",
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())

for label, cases in [("POSITIVE", POSITIVES), ("NEGATIVE", NEGATIVES)]:
    for text in cases:
        r = check(text)
        print(label, repr(text), "->", r)
```

Set `THRESHOLD` in `server.py` so **every negative case passes (`blocked: false`)**. A missed positive is acceptable for a hackathon demo; blocking an ordinary coding prompt live on stage is not. Restart the server after changing `THRESHOLD`.

Send Dev A: the final `THRESHOLD` you landed on, and your measured 1-chunk / 8-chunk latency (they need it to set `LAYA_TIMEOUT`).

Then help with Phase 4 (demo prep) in `PLAN (1).md`, and be ready to keep `server.py` running through the demo.
