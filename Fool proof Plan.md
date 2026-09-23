Foolproofed + two-developer build plan: HIPAA Safe Harbor de-identifier hook

 Context

 PLAN (1).md is a 90-minute hackathon build: a Claude Code UserPromptSubmit hook that hard-blocks prompts containing HIPAA Safe Harbor identifiers. Regex covers 8 categories, and an optional local Laya model covers 8 more. I checked it against this machine (Windows 11, Git Bash, Python 3.12, a project path with spaces, inside OneDrive) and against the installed laya 0.3.11 source. Several steps fail silently here, and the worst outcome is the hook not running and letting a fake SSN through on stage. There are now two developers, so the plan is split into two self-contained tracks that meet at one HTTP contract. Each developer can hand their file to their own Claude Code session.

 On approval, I will write these files to the project dir

 1. PLAN (1).md: rewritten as the shared overview. It covers goal, scope, the 18 categories, the contract, the timeline, the checkpoint, integration, the demo, and the verified-findings table.
 2. PLAN_DEV_A_hook.md: Dev A's self-contained track (below).
 3. PLAN_DEV_B_laya.md: Dev B's self-contained track (below).
 4. Then I'll kick off pip install fastapi uvicorn and the model prefetch in the background on this machine (Dev B's setup, and the long pole). Then stop; building is each dev's job.

 Each dev file repeats the contract, the checkpoint, and its own machine gotchas, so neither dev needs to read the other's file.

 ---

 Shared (goes in PLAN (1).md)

 Machine assignment:
 - Dev B works on this machine. laya, torch, and the ~800MB checkpoint belong here, and it's the demo machine.
 - Dev A can work anywhere. The hook is stdlib-only. Files get copied here for integration.
 - If Dev A is on a Mac or Linux laptop, the hook command differs. Write the settings for this machine (Windows) at integration.

 Contract (agree first, 2 min):
 - POST http://127.0.0.1:8420/check with body {"text": str} returns {"blocked": bool, "category": str, "phi_prob": float}
 - GET http://127.0.0.1:8420/health returns {"status": "ok"}
 - Dev A owns .claude/hooks/pii_check.py, .claude/settings.json, test_hook.py.
 - Dev B owns server.py, test_server.py.
 - Nobody edits the other's files before integration.

 Timeline:

 ┌────────┬───────────────────────────────────────────────────────────────────────────────────────────────────────┬───────────────────────────────────────────────────────┐
 │  min   │                                        Dev A (hook, required)                                         │             Dev B (Laya server, stretch)              │
 ├────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
 │ 0–5    │ Create .claude/hooks/                                                                                 │ Confirm fastapi/uvicorn installed + model prefetch    │
 │        │                                                                                                       │ running                                               │
 ├────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
 │ 5–30   │ Hook + settings + test_hook.py all PASS + live test                                                   │ server.py while the model downloads; start it; first  │
 │        │                                                                                                       │ POST works                                            │
 ├────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
 │ ~30    │ 🛑 CHECKPOINT (both report): A reports whether the live block works; B reports the server is up and   │                                                       │
 │        │ gives warm latency for 1 and 8 chunks. User decides go / skip Laya.                                   │                                                       │
 ├────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
 │ 30–55  │ Add the Laya client block to the hook; verify fail-open with the server stopped                       │ test_server.py; tune THRESHOLD until all negatives    │
 │        │                                                                                                       │ pass; send A the value + latency                      │
 ├────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
 │ 55–75  │ Integration together on this machine (see below)                                                      │                                                       │
 ├────────┼───────────────────────────────────────────────────────────────────────────────────────────────────────┼───────────────────────────────────────────────────────┤
 │ last   │ Demo prompts + pitch                                                                                  │ Pre-flight checklist                                  │
 │ 15     │                                                                                                       │                                                       │
 └────────┴───────────────────────────────────────────────────────────────────────────────────────────────────────┴───────────────────────────────────────────────────────┘

 On skip Laya: B helps harden test_hook.py edge cases and demo prep, and server.py stays out of the demo. Only the hook integration is gated by the checkpoint. B builds server.py beforehand because it can't break the required deliverable.

 Integration (this machine):
 1. Copy A's files into .claude/hooks/, .claude/settings.json, test_hook.py.
 2. Start B's server.
 3. A adds the Laya end-to-end rows to test_hook.py.
 4. python test_hook.py is all PASS.
 5. Live test in a second terminal: claude in this dir, confirm /hooks lists the hook, submit the demo prompts. Do not restart the builder session: hooks load at session start, so the builder stays unhooked and can still take GitHub URLs.

 Demo pre-flight + honesty lines: /health ok → test_hook.py all PASS → hook listed in /hooks.
 - Only the typed prompt is scanned. @-file contents, images, and tool output aren't.
 - Laya only checks the first ~5.6k characters; regex checks the whole prompt.
 - Known false positives: bare 10-digit numbers look like phones, and any http(s) URL is blocked.
 - Regex fails closed; Laya fails open.

 Verified findings table. I'll carry this into PLAN (1).md so both devs know why each deviation from the original plan exists:
 - python3 is the Store stub on this machine
 - The project path has a space
 - Hooks are snapshotted at session start
 - stdin is cp1252, so emoji crash the hook and it fails open
 - The checkpoint was not downloaded by pip
 - fastapi/uvicorn are missing
 - Laya has a 512-token window and right-truncates input
 - localhost → ::1 connect delay on Windows
 - The system proxy applies to urllib
 - torch is CPU-only
 - Blocking on any-confidence choice != none
 - Regex holes (parenthesized phones, all-digit "VINs", 127.0.0.1)
 - The bundled laya.serve binds 0.0.0.0

 ---

 PLAN_DEV_A_hook.md (Dev A)

 Goal: the required deliverable, a regex hard-block hook, working live. Stdlib only.

 .claude/hooks/pii_check.py:
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

 # <-- Laya client block goes here after the checkpoint
 sys.exit(0)

 .claude/settings.json (Windows form, for the demo machine):
 { "hooks": { "UserPromptSubmit": [ { "hooks": [ {
   "type": "command",
   "command": "python \"$CLAUDE_PROJECT_DIR/.claude/hooks/pii_check.py\"",
   "timeout": 15
 } ] } ] } }
 - Use python, not python3 (Store stub here), and no shebang or chmod.
 - Keep the quotes, because the path has a space.
 - Fallback if the hook doesn't fire: an absolute forward-slash path, C:/Users/aryan/OneDrive/Desktop/Ideas/Claude Build Day/.claude/hooks/pii_check.py.

 test_hook.py: a stdlib table of (prompt, expected_exit) pairs, run through subprocess.run([sys.executable, HOOK], input=json.dumps({"prompt": p}, ensure_ascii=False).encode("utf-8")). ensure_ascii=False is required, or the unicode row never exercises the UTF-8 fix. It prints PASS/FAIL and exits nonzero on any FAIL.
 - Expect 2:
   - 123-45-6789
   - 1HGCM82633A004352
   - 192.168.1.10
   - (555) 123-4567
   - 555.123.4567
   - an email address
   - https://example.com/patient/42
 - Expect 0:
   - help me refactor this function
   - bump version to 3.14.159
   - run it on 127.0.0.1:8420
   - rename useStateReducerXY
   - café — naïve résumé 😀

 Live test (mandatory; the only proof the hook is wired):
 1. Open a second terminal and run claude in the project dir.
 2. Confirm /hooks lists the hook.
 3. Submit patient ssn 123-45-6789. It should be blocked with the message and no reply.

 Report at the checkpoint: the test_hook.py result and the live result.

 After go: add the Laya client (after the regex loop, before sys.exit(0)):
 LAYA_TIMEOUT = 3  # replace with ~2x Dev B's measured 8-chunk latency, cap 8
 try:
     opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
     req = urllib.request.Request("http://127.0.0.1:8420/check",
         data=json.dumps({"text": prompt}).encode("utf-8"),
         headers={"Content-Type": "application/json"}, method="POST")
     with opener.open(req, timeout=LAYA_TIMEOUT) as resp:
         r = json.loads(resp.read())
     if r.get("blocked"):
         block(f"Blocked: Laya flagged a possible '{r['category']}' Safe Harbor identifier (p={r['phi_prob']:.2f}).")
 except Exception:
     pass  # fail open; block()'s SystemExit is BaseException so it is NOT swallowed. Never make this a bare except.
 - Use 127.0.0.1, not localhost: the IPv6 fallback delay on Windows can eat the timeout.
 - ProxyHandler({}) bypasses any system proxy.
 - Verify: with the server stopped, clean prompts still pass and all regex rows still PASS.

 ---

 PLAN_DEV_B_laya.md (Dev B, on this machine)

 Goal: a local Laya server that implements the contract. It must never leave 127.0.0.1: sending PHI to any hosted API defeats the tool.

 Setup (started on approval):
 - pip install fastapi uvicorn
 - Prefetch + smoke test. In Git Bash: USE_TF=0 python -c "import laya; a=laya.load('convaiinnovations/laya', device='cpu'); print(a.predict('hello there', {'q':{'type':'noul','instructions':'Is this a greeting?'}}))". In PowerShell, prefix with $env:USE_TF='0'; instead.
 - If load() hangs, USE_TF wasn't set.

 server.py:
 - os.environ.setdefault("USE_TF", "0") before importing laya.
 - torch.set_num_threads(8): tune it, and don't exceed physical cores (24 here).
 - agent = laya.load("convaiinnovations/laya", device="cpu"). torch is the CPU build; don't install CUDA torch tonight.
 - QUESTIONS: the original plan's safe_harbor_category choice question (name/geographic/date/id_number/none). Word the instruction around `prompt` and pass the state as {"prompt": chunk}, which is Laya's preset convention (presets.py:82-96). Fall back to a plain string if accuracy looks worse.
 - Chunking: the model sees about 320 tokens of state and right-truncates. Split text into 800-char chunks with 100-char overlap, max 8, and run them through agent.predict_batch(...).
 - Scoring: per chunk, phi_prob = 1 - probabilities["none"]. Take the max chunk and return {"blocked": phi_prob >= THRESHOLD, "category": <top non-none key>, "phi_prob": ...}. Start with THRESHOLD = 0.6.
 - GET /health, and one warm-up agent.predict(...) at module load.
 - If the warm-up raises options exceed head_max_len=192, shorten the criteria text (id_number is the longest).
 - Run: python -m uvicorn server:app --host 127.0.0.1 --port 8420. Don't use the bundled laya.serve: it binds 0.0.0.0.

 Report at the checkpoint: server up, plus warm latency for a 1-chunk and an 8-chunk request.

 After go, test_server.py: POST straight to /check and print phi_prob for each case.
 - Positives: send this to Sarah Chen; she was born March 3rd 1961; lives at 42 Elm Street, Springfield; MRN 00482913
 - Negatives: fix the off-by-one in getUserName; schedule the deploy for Friday; add a zip code field to the form
 - Set THRESHOLD so every negative passes. A missed positive is acceptable; a blocked coding prompt on stage is not.
 - Send Dev A the final THRESHOLD and latency.

 ---

 Verification (end-to-end, this machine)

 1. Server stopped: python test_hook.py is all PASS. This proves regex works alone and Laya fails open.
 2. Server up: test_hook.py including the Laya rows is all PASS.
 3. Second-terminal live session:
    - SSN → blocked.
    - A name in context → blocked, with the category shown.
    - A clean coding prompt → normal reply.
 4. Kill the server and submit a clean prompt: it still gets a reply within about LAYA_TIMEOUT.