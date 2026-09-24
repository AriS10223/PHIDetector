# PII Leak Detector for Claude Code — Build Plan

Read this whole file before writing any code. This is a ~90 minute build for a live hackathon demo — the checkpoint below is a hard stop, not a suggestion.

## Goal
A Claude Code hook that hard-blocks a user's prompt *before Claude ever sees it* if the prompt contains PII (personally identifiable information).

## Scope — do not expand beyond this
- **Hook point:** `UserPromptSubmit` only. Do not build `PreToolUse`, file-write scanning, or Bash-command scanning tonight — out of scope.
- **Surface:** Claude Code only. Claude Desktop and claude.ai are not in scope — they're future work, mention only in the demo pitch.
- **Behavior on detection:** Hard block. The prompt must be erased before Claude sees it, not just flagged.
- **Detection, two layers:**
  1. Regex (required, build first) — catches structured PII: email, phone, SSN, credit card patterns.
  2. Laya (stretch goal, gated behind a checkpoint — see below) — a locally-hosted, non-generative classifier (`convaiinnovations/laya` on Hugging Face) that catches *unstructured* PII regex misses, e.g. a name mentioned in context or an address written in prose.

## Why Laya runs locally — do not swap this for a hosted API
The point of this tool is to stop PII leaking. Sending the prompt text to a hosted third-party API just to check whether it's PII would defeat the purpose. `pip install laya` runs the model on-device — keep it that way even if a hosted alternative seems simpler.

---

## Phase 1 — Setup (target: under 5 min)
1. Confirm you're working in a fresh, empty directory.
2. Create `.claude/hooks/`.
3. If you're going to attempt the Laya stretch goal at all, run `pip install laya` now, in the background, before anything else in this plan. It pulls in torch + transformers plus an ~800MB checkpoint — it's the slowest, least predictable step tonight, so it needs the most lead time.

## Phase 2 — Regex hard-block hook (target: under 25 min). This is the required deliverable.

Create `.claude/hooks/pii_check.py`:

```python
#!/usr/bin/env python3
import sys, json, re

data = json.load(sys.stdin)
prompt = data.get("prompt", "")

PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
    "phone": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
    "credit_card": r"\b(?:\d[ -]*?){13,16}\b",
}

for label, pattern in PATTERNS.items():
    if re.search(pattern, prompt):
        print(f"Blocked: possible {label} detected in prompt.", file=sys.stderr)
        sys.exit(2)

sys.exit(0)
```

Make it executable: `chmod +x .claude/hooks/pii_check.py`

Create `.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/pii_check.py" } ] }
    ]
  }
}
```

Exit code 2 on `UserPromptSubmit` erases the prompt and shows stderr to the user only — Claude never sees the blocked content. Don't switch this to the JSON-output form; the exit-code form is simpler and there's no time to debug JSON schema issues live.

This exact script has already been hand-tested (piped JSON via stdin): it blocks on SSN, blocks on email, passes clean prompts, and correctly does *not* false-positive on a short digit string that isn't a full phone number. Trust this logic — wire it in as-is rather than rewriting it.

**Known limitation, acceptable for tonight:** the credit-card pattern is loose and could flag long unrelated digit sequences (e.g. an ID number). Not worth fixing under this time budget — regex is the must-have safety net, not the polish target.

### Self-test before moving on — do this yourself, don't wait for a live Claude Code session
```bash
echo '{"prompt": "help me write a sort function"}' | .claude/hooks/pii_check.py; echo "exit: $?"   # expect 0
echo '{"prompt": "my ssn is 123-45-6789"}' | .claude/hooks/pii_check.py; echo "exit: $?"            # expect 2
echo '{"prompt": "email me at test@example.com"}' | .claude/hooks/pii_check.py; echo "exit: $?"     # expect 2
```

Then confirm it live: register the hook in a real Claude Code session in this directory, submit a prompt containing a fake SSN, confirm it's blocked before Claude responds.

---

## 🛑 CHECKPOINT — STOP HERE. Do not start Phase 3 without explicit confirmation.

Once Phase 2 is working and self-tested, stop and report back in chat:
- Whether the regex hook is confirmed working (both piped test and live test)
- Whether `pip install laya` has finished downloading
- Roughly how much time is left

Wait for the user to explicitly say to proceed before touching Phase 3. If they say to skip it, move straight to Phase 4 with what you have — a working regex-only hard-block is a complete deliverable on its own.

---

## Phase 3 — Laya stretch goal (only after the user says go)

Create `server.py` at the project root:

```python
from fastapi import FastAPI
import laya

app = FastAPI()
agent = laya.load("convaiinnovations/laya")  # loads once, at server startup — not per-request

QUESTIONS = {
    "contains_pii": {
        "type": "noul",
        "instructions": "Does this text contain information that could identify a specific real person?",
        "criteria": {
            "true": "Contains a name, address, phone/email, ID number, or other detail tied to an identifiable individual",
            "false": "No identifying information about a real person"
        }
    }
}

@app.post("/check")
def check(body: dict):
    result = agent.predict(body["text"], QUESTIONS)
    return result["answers"]["contains_pii"]
```

Start it before the demo, as a separate background process — not inside the hook: `uvicorn server:app --port 8420`

**Known gotcha:** if `laya.load()` hangs, it's `transformers` deadlocking on a TensorFlow probe at import. Set `USE_TF=0` in the environment and retry.

Update `.claude/hooks/pii_check.py` — add this block after the regex `for` loop, before the final `sys.exit(0)`:

```python
import urllib.request

try:
    req = urllib.request.Request(
        "http://localhost:8420/check",
        data=json.dumps({"text": prompt}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        result = json.loads(resp.read())
    if result.get("noul", 0) > 0.6:
        print(f"Blocked: Laya flagged possible PII (confidence {result['noul']:.2f}).", file=sys.stderr)
        sys.exit(2)
except Exception:
    pass  # fail OPEN if the Laya server isn't reachable — don't let a server hiccup block every prompt
```

Deliberate design choices, don't change these:
- Uses stdlib `urllib.request`, not the `requests` package — keeps the hook script dependency-free so it starts fast and reliably.
- Fails open (allows the prompt through) if the Laya server errors or times out. Regex is the safety net; Laya is the enhancement layer, and it must never be able to break the whole hook.
- 2-second timeout on the request, so a slow/stuck model call can't hang the user's prompt submission.

### Test Phase 3
Use 2–3 hand-picked examples with no obvious regex pattern (a name dropped in context, an address written in prose) alongside a clearly clean prompt. Confirm Laya differentiates between them. The base checkpoint was not fine-tuned specifically for PII detection, so don't expect perfection — note the confidence score it returns, it's a fine thing to show live even when it's not 100% certain.

---

## Phase 4 — Demo prep (last ~15 min, regardless of which phase you reached)
- Line up 2–3 prompts to type live in a real session: one obvious regex catch, one Laya catch if Phase 3 was built, one clean pass-through
- One-line pitch to have ready: PII detection runs entirely on-device here — nothing leaves the machine just to get checked for whether it's sensitive
