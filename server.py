"""Local HIPAA Safe Harbor decision server backed by the `laya` classifier.

Runs entirely on-device, bound to 127.0.0.1 only. Never point this at a hosted API.

Contract (agreed with Dev A's hook):
    POST /check  {"text": str} -> {"blocked": bool, "category": str, "phi_prob": float}
    GET  /health              -> {"status": "ok"}

Run:
    python3 -m uvicorn server:app --host 127.0.0.1 --port 8420
    (or)  python3 server.py
"""
import os
os.environ.setdefault("USE_TF", "0")  # must be set before `import laya` (transformers TF probe can deadlock)

# Once the checkpoint is cached, forbid the Hugging Face client from making ANY network call.
# Prefetch first (see PLAN_DEV_B_laya.md Phase 1); until then the first `laya.load` downloads it.
_HF_CACHE = os.path.expanduser("~/.cache/huggingface/hub/models--convaiinnovations--laya")
if os.path.isdir(_HF_CACHE):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import threading
import time

import torch

# CPU-only inference. This Mac has 12 physical cores; leave headroom for Claude Code + the terminal.
torch.set_num_threads(int(os.environ.get("LAYA_THREADS", "6")))

import laya
from fastapi import FastAPI

app = FastAPI(title="Safe Harbor local check")

# Loaded once at import time, not per-request. First load downloads the checkpoint (~800MB).
agent = laya.load("convaiinnovations/laya", device="cpu")

# One yes/no ("noul") question per Safe Harbor category. Tested against a single 5-way choice head
# and against laya's guard preset: the per-category questions separated positives from ordinary
# coding prompts far better (min positive 0.85 vs max negative 0.60 on test_server.py's cases),
# while the choice head scored "add a zip code field to the form" at 0.90.
# Keep the wording short: each question + state must fit laya's 512-token window (head <= 192).
QUESTIONS = {
    "name": {
        "type": "noul",
        "instructions": "Does `prompt` mention a specific real person by name?",
    },
    "geographic": {
        "type": "noul",
        # "street address, city or ZIP code value" scored 127.0.0.1 and any 5-digit number as an
        # address (0.76-0.93). This wording keeps real addresses at >=0.80 and IPs/ticket numbers <=0.56.
        "instructions": "Does `prompt` contain a postal mailing address or home town for a specific person?",
    },
    "date": {
        "type": "noul",
        "instructions": "Does `prompt` state a specific person's birth, admission, discharge or death date, or an age over 89?",
    },
    # id_number uses TWO heads that must both agree (min of the two). Alone, the first scores
    # "the ticket number is 48213" at 0.83 and the second scores a sentence about HIPAA at 0.84;
    # the min of the pair stays <=0.60 on every negative while real record/plan/license/serial
    # numbers score >=0.93 on both.
    "id_number": {
        "type": "noul",
        "instructions": "Does `prompt` contain an actual medical record, insurance, license or device serial number value?",
    },
    "id_number_health": {
        "type": "noul",
        "instructions": "Does `prompt` contain a healthcare identifier for a person, like a medical record number, insurance member ID, or implanted device serial?",
    },
}
# Questions whose answers are combined into one category by taking the minimum.
PAIRED = {"id_number": ("id_number", "id_number_health")}

# Block when any category question answers "yes" with at least this probability.
# Tuned with test_server.py: every NEGATIVE must come back blocked=false. Measured margin:
# max negative 0.60 ("the HIPAA Safe Harbor standard lists 18 identifier categories"), min positive 0.85.
THRESHOLD = float(os.environ.get("LAYA_THRESHOLD", "0.75"))

# laya's context window is ~512 tokens with the question head capped at 192, and a plain string
# state is right-truncated. Chunk instead of silently dropping the tail of a long prompt.
CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
# Measured on this Mac (CPU, 4 questions/chunk): ~1.0s per code-heavy chunk, ~0.65s per prose chunk,
# and thread count makes no difference. 8 chunks would be ~8s per prompt, too slow for a hook.
# 4 chunks = first ~2.9k characters scanned, ~4s worst case. Regex in the hook covers the whole prompt.
MAX_CHUNKS = int(os.environ.get("LAYA_MAX_CHUNKS", "4"))

# uvicorn runs sync endpoints in a threadpool; serialize torch calls so two concurrent
# requests don't fight over the same intra-op thread pool.
_predict_lock = threading.Lock()


def chunk_text(text: str):
    chunks = []
    start = 0
    while start < len(text) and len(chunks) < MAX_CHUNKS:
        chunks.append(text[start:start + CHUNK_SIZE])
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks or [""]


def score(text: str) -> dict:
    chunks = chunk_text(text)
    # {"prompt": ...} matches laya's own preset convention (the instructions reference `prompt`).
    states = [{"prompt": c} for c in chunks]
    with _predict_lock:
        results = agent.predict_batch(states, QUESTIONS)

    # Highest "yes" probability across every (chunk, category) pair. Paired heads are
    # folded into one category first by taking the min, so both must agree.
    best_prob = 0.0
    best_category = "none"
    categories = [q for q in QUESTIONS if not any(q in pair[1:] for pair in PAIRED.values())]
    per_category = {q: 0.0 for q in categories}
    for r in results:
        ans = {q: a["noul"] for q, a in r["answers"].items()}
        for cat, heads in PAIRED.items():
            ans[cat] = min(ans[h] for h in heads)
        for q in categories:
            p = ans[q]
            per_category[q] = max(per_category[q], p)
            if p > best_prob:
                best_prob, best_category = p, q

    blocked = best_prob >= THRESHOLD
    return {
        "blocked": blocked,
        "category": best_category if blocked else "none",
        "phi_prob": round(best_prob, 4),
        # Extra diagnostics; the hook ignores keys it doesn't know.
        "top_category": best_category,
        "per_category": {q: round(p, 4) for q, p in per_category.items()},
        "chunks": len(chunks),
    }


# Warm-up: pay first-call latency at startup, not on the first real request.
_t0 = time.time()
score("hello")
print(f"[server] laya warm, first call {time.time() - _t0:.2f}s, "
      f"THRESHOLD={THRESHOLD} MAX_CHUNKS={MAX_CHUNKS} threads={torch.get_num_threads()} "
      f"offline={os.environ.get('HF_HUB_OFFLINE', '0')}")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/check")
def check(body: dict):
    text = body.get("text", "")
    if not isinstance(text, str):
        text = str(text)
    return score(text)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8420, log_level="warning")
