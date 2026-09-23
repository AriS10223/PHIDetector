# Dev A — Regex hard-block hook (required deliverable)

Read `PLAN (1).md` first for the shared goal/scope/contract/timeline. This file is everything you need to build your track without touching Dev B's files.

**Goal:** a Claude Code `UserPromptSubmit` hook, stdlib Python only, that hard-blocks a prompt containing an obviously-patterned HIPAA Safe Harbor identifier (SSN, phone/fax, email, URL, IP, VIN, card/account-shaped number) before Claude ever sees it. Later (after the team checkpoint), you'll also wire in a call to Dev B's local Laya server for the categories regex can't catch.

You own: `.claude/hooks/pii_check.py`, `.claude/settings.json`, `test_hook.py`. Don't edit `server.py` or `test_server.py` — that's Dev B's.

**Contract you call after the checkpoint:** `POST http://127.0.0.1:8420/check` with `{"text": str}` → `{"blocked": bool, "category": str, "phi_prob": float}`; `GET http://127.0.0.1:8420/health` → `{"status": "ok"}`.

## Phase 1 — Setup (target: under 5 min)
1. Confirm you're working in `C:\Users\aryan\OneDrive\Desktop\Ideas\Claude Build Day`.
2. Create `.claude/hooks/`.

## Phase 2 — Regex hard-block hook (target: under 25 min)

Create `.claude/hooks/pii_check.py`:

```python
import sys, json, re, urllib.request

def block(msg):
    print(msg, file=sys.stderr)
    sys.exit(2)

try:
    data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace"))
    prompt = data.get("prompt") or ""
except Exception:
    block("Blocked: PHI check could not read the prompt (failing closed).")

PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "ssn": r"\b\d{3}[- ]\d{2}[- ]\d{4}\b",
    "phone_or_fax": r"(?:\+?1[\s.-]?)?\(?\b\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}\b",
    "account_or_card_number": r"\b(?:\d[ -]*?){13,16}\b",
    "url": r"(?i)\bhttps?://\S+",
    "ip_address": r"\b(?!127\.)(?:\d{1,3}\.){3}\d{1,3}\b",
    "vehicle_vin": r"\b(?=[A-HJ-NPR-Z0-9]*\d)(?=[A-HJ-NPR-Z0-9]*[A-HJ-NPR-Z])[A-HJ-NPR-Z0-9]{17}\b",
}
for label, pattern in PATTERNS.items():
    if re.search(pattern, prompt):
        block(f"Blocked: possible {label} detected (HIPAA Safe Harbor identifier).")

# <-- after the team checkpoint, the Laya client block goes here -->
sys.exit(0)
```

Notes on why this differs from a naive version (full detail in `PLAN (1).md`'s machine notes):
- No shebang, no `chmod +x` — `python3` on this machine is a non-functional Store stub. The hook is invoked explicitly with `python` from `settings.json`.
- Reads `sys.stdin.buffer` and decodes UTF-8 explicitly, with a **fail-closed** except clause — default stdin decoding on Windows Python is cp1252, and an emoji in the prompt would otherwise crash the script and let it through unblocked.
- No `re.IGNORECASE` on the whole set — VINs are uppercase by spec; case-insensitive VIN matching would create false positives on lowercase hex-like tokens.
- IP pattern excludes `127.0.0.1` via negative lookahead — that address is your own Laya server, not PHI.
- Phone pattern covers parenthesized and dot-separated formats, not just hyphenated.

**Known limitation, acceptable for tonight:** the account/card-number pattern is loose and could flag long unrelated digit sequences. Not worth fixing under this time budget.

Create `.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/pii_check.py\"", "timeout": 15 } ] }
    ]
  }
}
```

- Use `python`, not `python3`.
- Keep the quotes around the path — the project directory name has a space in it.
- If the hook doesn't fire in the live test, fall back to an absolute forward-slash path: `python "C:/Users/aryan/OneDrive/Desktop/Ideas/Claude Build Day/.claude/hooks/pii_check.py"`.

Exit code 2 on `UserPromptSubmit` erases the prompt and shows stderr to the user only — Claude never sees the blocked content.

### Self-test — `test_hook.py` (project root)

Stdlib only. A table of `(prompt, expected_exit)` pairs, each piped through `subprocess.run([sys.executable, HOOK], input=..., timeout=15)`. Print PASS/FAIL per row; exit nonzero if any row fails.

```python
import json, subprocess, sys

HOOK = ".claude/hooks/pii_check.py"

CASES = [
    ("help me refactor this function", 0),
    ("bump version to 3.14.159", 0),
    ("run it on 127.0.0.1:8420", 0),
    ("rename useStateReducerXY", 0),
    ("café — naïve résumé 😀", 0),
    ("patient ssn 123-45-6789", 2),
    ("vehicle VIN is 1HGCM82633A004352", 2),
    ("the server is at 192.168.1.10", 2),
    ("call me at (555) 123-4567", 2),
    ("call me at 555.123.4567", 2),
    ("email me at pat@example.com", 2),
    ("see https://example.com/patient/42", 2),
]

failed = 0
for prompt, expected in CASES:
    payload = json.dumps({"prompt": prompt}, ensure_ascii=False).encode("utf-8")
    r = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, timeout=15)
    ok = r.returncode == expected
    print(("PASS" if ok else "FAIL"), repr(prompt), "expected", expected, "got", r.returncode)
    failed += not ok

sys.exit(1 if failed else 0)
```

`ensure_ascii=False` is required on the `json.dumps` call — otherwise the unicode case is sent as `\u` escapes and never actually exercises the UTF-8 decode path.

Run: `python test_hook.py`. All rows must PASS before moving on.

### Live test (mandatory — the only thing that proves the hook is actually wired up)
1. Open a **second terminal**, `cd` into the project dir, run `claude`. (Don't restart your current builder session — hooks are snapshotted at session start, and you don't want to lose this session's context mid-build.)
2. Run `/hooks` and confirm `pii_check.py` is listed under `UserPromptSubmit`.
3. Submit: `patient ssn 123-45-6789`. Confirm it's blocked with the stderr message and Claude never responds.

## 🛑 CHECKPOINT — STOP HERE
Report in the shared chat/channel:
- `test_hook.py` result (all PASS?)
- Live test result (blocked as expected?)
- Roughly how much time is left

Wait for the team to say go before touching the Laya integration below. If told to skip it, move to Phase 4 (demo prep) with what you have — a working regex-only hard-block covering 8 of 18 categories is a complete deliverable on its own.

## After go — wire in Laya

Add this block to `pii_check.py`, after the regex `for` loop, before `sys.exit(0)`:

```python
LAYA_TIMEOUT = 3  # set to ~2x Dev B's measured 8-chunk latency, capped around 8
try:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        "http://127.0.0.1:8420/check",
        data=json.dumps({"text": prompt}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener.open(req, timeout=LAYA_TIMEOUT) as resp:
        r = json.loads(resp.read())
    if r.get("blocked"):
        block(f"Blocked: Laya flagged a possible '{r['category']}' Safe Harbor identifier (p={r['phi_prob']:.2f}).")
except Exception:
    pass  # fail OPEN — a Laya/server hiccup must never block every prompt
```

Design choices, don't change:
- `urllib.request`, not `requests` — keeps the hook dependency-free so it starts fast and reliably.
- `127.0.0.1`, not `localhost` — avoids the IPv6-connect-delay-then-fallback behavior on Windows that could eat the whole timeout.
- `ProxyHandler({})` — bypasses any system/registry proxy that might otherwise intercept the "localhost" call.
- Fails open on any exception. `block()` calls `sys.exit()`, which raises `SystemExit` — a `BaseException`, not caught by `except Exception`, so a genuine regex-driven block is never accidentally swallowed here. **Never** widen this to a bare `except:`.
- Ask Dev B for the tuned `LAYA_TIMEOUT` value once they've measured latency.

Verify:
- With Dev B's server **stopped**, clean prompts still pass and all regex `test_hook.py` rows still PASS (proves fail-open).
- With the server running, get the Laya end-to-end test cases from `PLAN (1).md`'s integration section, add them to `test_hook.py`, confirm all PASS.

Then proceed to Phase 4 in `PLAN (1).md`.
