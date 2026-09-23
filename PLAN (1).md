# HIPAA Safe Harbor De-Identifier for Claude Code — Build Plan (shared overview)

Read this whole file before writing any code. This is a ~90 minute build for a live hackathon demo — the checkpoint below is a hard stop, not a suggestion.

Two developers, two tracks, one HTTP contract:
- **Dev A** builds the required deliverable — see `PLAN_DEV_A_hook.md`.
- **Dev B** builds the Laya stretch goal — see `PLAN_DEV_B_laya.md`.

This file has the shared context both devs need: goal, scope, the 18 categories, the machine notes, the contract, the timeline, integration, and the demo.

## Goal
A Claude Code hook that hard-blocks a user's prompt *before Claude ever sees it* if the prompt contains an identifier covered by HIPAA's Safe Harbor de-identification standard (45 CFR § 164.514(b)(2)) — the fixed list of 18 identifier categories that, once all removed, means health data is no longer PHI.

## Scope — do not expand beyond this
- **Hook point:** `UserPromptSubmit` only. Do not build `PreToolUse`, file-write scanning, or Bash-command scanning tonight — out of scope.
- **Surface:** Claude Code only. Claude Desktop and claude.ai are not in scope — they're future work, mention only in the demo pitch.
- **Behavior on detection:** Hard block. The prompt must be erased before Claude sees it, not just flagged.
- **Detection categories: the 18 HIPAA Safe Harbor identifiers, not generic "PII."** This is the whole point of this version of the plan — grounded in a named regulatory standard, not an ad hoc list.

## The 18 categories, and how each is covered tonight

Addressable with a text-based hook — **regex**, owned by Dev A (fixed pattern, fast, precise):
1. Telephone numbers
2. Fax numbers *(same digit pattern as phone — can't distinguish by pattern alone, flagged together)*
3. Email addresses
4. Social Security numbers
5. Account numbers *(partial — digit sequences shaped like card/account numbers; free-form account numbers aren't reliably regex-able)*
6. Vehicle identifiers *(VIN has a fixed 17-character format — regex-able)*
7. URLs
8. IP addresses

Addressable with a text-based hook — **Laya**, owned by Dev B (needs context/judgment, no fixed pattern):
9. Names
10. Geographic subdivisions smaller than a state (street address, city, county, ZIP) — a bare ZIP-code regex is too false-positive-prone (5-digit numbers are everywhere in code/data) without context
11. Dates tied to a specific individual (birth/admission/discharge/death date, ages 90+) — a bare date regex can't tell "the deadline is the 5th" from "patient DOB is the 5th"; needs judgment
12. Medical record numbers
13. Health plan beneficiary numbers
14. Certificate/license numbers
15. Device identifiers/serial numbers
16. Any other unique identifying number, characteristic, or code (the Safe Harbor catch-all)

**Out of scope tonight — not achievable with a text-only hook, be upfront about this in the pitch:**
17. Biometric identifiers (finger/voice prints) — raw biometric data, not something that shows up as text in a chat prompt
18. Full-face photographic images — image content, not text; would need image analysis on attachments, genuinely different scope

16 of 18 addressed through text is still a strong, honest claim — don't oversell the other two.

## Why Laya runs locally — do not swap this for a hosted API
The point of this tool is to stop PHI leaking. Sending the prompt text to a hosted third-party API just to check whether it's PHI would defeat the purpose, and is a worse look for a HIPAA-flavored pitch specifically. `pip install laya` runs the model on-device, bound to `127.0.0.1` only — keep it that way.

---

## Machine notes (why the two dev files deviate from a naive read of this plan)

Verified against this build machine (Windows 11, Git Bash primary shell, Python 3.12 at `...\Python312\python.exe`, project path `C:\Users\aryan\OneDrive\Desktop\Ideas\Claude Build Day` — contains a space, inside OneDrive) and against the installed `laya` 0.3.11 package source:

- `python3` resolves only to the Windows Store stub here — use `python`, not `python3`, and don't rely on a shebang + `chmod +x`.
- The project path has a space — quote it everywhere it's used in a command.
- Claude Code snapshots hooks at session start — writing `.claude/settings.json` mid-session does nothing until a **new** session picks it up.
- Windows Python reads stdin as cp1252 by default — a prompt with emoji/smart quotes would raise `UnicodeDecodeError` and, if unhandled, exit non-2, letting the prompt through. The hook must decode stdin as UTF-8 explicitly and fail **closed** (exit 2) if parsing fails at all.
- `pip install laya` does **not** download the model checkpoint — that happens on first `laya.load(...)`, and it's the slowest, least predictable step tonight (~800MB). Kick it off in the background immediately.
- `fastapi` / `uvicorn` are not installed by `pip install laya` — Dev B installs them separately.
- Laya's default agent has a ~512-token context window (question head ≤192 tokens) and **right-truncates** a plain string state — a long prompt would have its tail silently dropped. Dev B's server chunks the text instead of passing it whole.
- Use `http://127.0.0.1:8420`, not `http://localhost:8420` — on Windows, a refused IPv6 (`::1`) connect attempt before falling back to IPv4 can burn a large chunk of a short timeout.
- `urllib.request` on Windows honors the system/registry proxy by default — a venue or corporate proxy could otherwise intercept the localhost call. Use an explicit `ProxyHandler({})`.
- torch installed here is the **CPU** build (`2.14.0+cpu`) even though an RTX 4070 is present. Don't try to install CUDA torch tonight — not enough time to validate it. 24 physical cores are available for `torch.set_num_threads(...)`.
- Blocking on *any* nonzero confidence that `choice != "none"` is too trigger-happy for a checkpoint that wasn't fine-tuned for this exact task — Dev B thresholds on `1 - P(none)` instead, tuned against real negative examples.
- Original regex holes fixed: `(555) 123-4567` and `555.123.4567` weren't matched by a plain `\d{3}-\d{3}-\d{4}`-style pattern; a bare 17-digit number would match the VIN pattern (VINs require at least one letter); `127.0.0.1` itself would trip the IP pattern.
- The `laya` package ships its own `laya.serve` HTTP server — do not use it. It defaults to binding `0.0.0.0` (LAN-exposed) and preloading every checkpoint. Dev B writes a small custom `server.py` bound to `127.0.0.1` instead.

---

## Contract (agree on this first, 2 min)
- `POST http://127.0.0.1:8420/check` with body `{"text": str}` returns `{"blocked": bool, "category": str, "phi_prob": float}`
- `GET http://127.0.0.1:8420/health` returns `{"status": "ok"}`
- **Dev A** owns `.claude/hooks/pii_check.py`, `.claude/settings.json`, `test_hook.py`.
- **Dev B** owns `server.py`, `test_server.py`.
- Nobody edits the other's files before integration.

## Machine assignment
- **Dev B works on this machine.** laya, torch, and the checkpoint belong here, and this is the demo machine.
- **Dev A can work anywhere.** The hook is stdlib-only Python. Files get copied here for integration.
- If Dev A is on a different OS, their local hook command may differ — the `.claude/settings.json` actually used for the demo is the Windows form in `PLAN_DEV_A_hook.md`, written at integration time.

## Timeline

| min | Dev A (hook, required) | Dev B (Laya server, stretch) |
|---|---|---|
| 0–5 | Create `.claude/hooks/` | Confirm fastapi/uvicorn installed + model prefetch running |
| 5–30 | Hook + settings + `test_hook.py` all PASS + live test | `server.py` while the model downloads; start it; first POST works |
| **~30** | **🛑 CHECKPOINT (both report):** A reports whether the live block works; B reports the server is up and gives warm latency for a 1-chunk and 8-chunk request. **User decides go / skip Laya.** | |
| 30–55 | Add the Laya client block to the hook; verify fail-open with the server stopped | `test_server.py`; tune `THRESHOLD` until all negatives pass; send A the value + latency |
| 55–75 | **Integration together on this machine** (below) | |
| last 15 | Demo prompts + pitch | Pre-flight checklist |

**On skip Laya:** Dev B helps harden `test_hook.py` edge cases and demo prep; `server.py` stays out of the demo. Only the *hook integration* is gated by the checkpoint — a working regex-only hard-block covering 8 of 18 categories is a complete deliverable on its own. Dev B builds `server.py` before the checkpoint because that work alone can't break the required deliverable.

## Integration (this machine, after the checkpoint go)
1. Copy Dev A's files into `.claude/hooks/`, `.claude/settings.json`, `test_hook.py`.
2. Start Dev B's server.
3. Dev A adds the Laya end-to-end rows to `test_hook.py`.
4. `python test_hook.py` is all PASS.
5. Live test in a **second terminal**: run `claude` in this dir, confirm `/hooks` lists the hook, submit the demo prompts. Do not restart the builder session — hooks load at session start, so the builder session stays unhooked (useful: it can still take GitHub URLs, e.g. for `git push`) until it's restarted.

## Phase 4 — Demo prep (last ~15 min, regardless of which phase you reached)
- Line up 3–4 prompts to type live: one regex catch (e.g. SSN), one Laya catch if built (e.g. a name in context), one clean pass-through.
- Pre-flight, in order: `GET http://127.0.0.1:8420/health` ok → `python test_hook.py` all PASS → hook listed in `/hooks`.
- One-line pitch to have ready: this maps directly onto the HIPAA Safe Harbor identifier list — 16 of the 18 categories are addressed through text analysis; the remaining 2 (biometrics, full-face photos) are image/audio data and out of scope for a chat-prompt hook.
- Honesty lines, ready if asked:
  - Only the typed prompt text is scanned — `@`-mentioned file contents, pasted images, and tool output aren't.
  - Laya only checks the first ~5.6k characters (8 chunks); regex checks the whole prompt.
  - Known regex false positives: bare 10-digit numbers (e.g. Unix timestamps) look like phone numbers, and any http(s) URL is blocked.
  - If asked "is this actually HIPAA compliant": Safe Harbor requires removing *all 18* categories with no gaps, so this is a solid demonstration of the approach, not a certified compliance product — say that plainly if it comes up.

## Verification (end-to-end)
1. Server stopped: `python test_hook.py` is all PASS. Proves regex works alone and Laya fails open.
2. Server up: `test_hook.py` including the Laya rows is all PASS.
3. Second-terminal live session: SSN → blocked; a name in context → blocked with category shown; a clean coding prompt → normal reply.
4. Kill the server and submit a clean prompt: it still gets a reply within about `LAYA_TIMEOUT`.
