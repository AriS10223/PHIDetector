import json, os, subprocess, sys, tempfile, urllib.request

HOOK = ".claude/hooks/pii_check.py"
STORE = tempfile.mkdtemp(prefix="phi-mask-test-")
ENV = {**os.environ, "LOCALAPPDATA": STORE, "PHI_MASK_NO_CLIPBOARD": "1"}
SESSION = "test-session"
MAP_PATH = os.path.join(STORE, "phi-mask", SESSION + ".json")

# Tokens are built, never written literally: this project's live hooks unmask any literal
# token in a tool call, which would silently rewrite this file while editing it.
def tok(label, n):
    return "[[" + "PHI_" + label + "_" + str(n) + "]]"

def run(payload):
    payload = {"session_id": SESSION, **payload}
    r = subprocess.run([sys.executable, HOOK], input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                       capture_output=True, timeout=15, env=ENV)
    out = json.loads(r.stdout) if r.stdout.strip() else None
    return r.returncode, out, r.stderr.decode("utf-8", errors="replace")

failed = 0
def check(name, ok):
    global failed
    print(("PASS" if ok else "FAIL"), name)
    failed += not ok

# --- block / allow (unchanged behavior: PHI prompts never reach Claude) ---
CASES = [
    ("help me refactor this function", 0),
    ("bump version to 3.14.159", 0),
    ("the laya server listens on 127.0.0.1", 0),
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
for i, (prompt, expected) in enumerate(CASES, 1):
    code, _, _ = run({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
    check(f"case {i}: exit {expected} (got {code})", code == expected)

# --- End-to-end through Laya: regex can't see any of these. Only run when server.py is up. ---
LAYA_CASES = [
    ("Sarah Chen has diabetes, write her a care plan", 2),
    ("she was born March 3rd 1961", 2),
    ("the patient lives at 42 Elm Street, Springfield", 2),
    ("MRN 00482913", 2),
    ("Mr. Okafor, age 92, was discharged yesterday", 2),
    ("add a zip code field to the form", 0),
    ("rename the patient table to encounters in the migration", 0),
    ("write a unit test for the date parser", 0),
]

def laya_up():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8420/health", timeout=3) as resp:
            return json.loads(resp.read()).get("status") == "ok"
    except Exception:
        return False

if laya_up():
    print("Laya server up: running Laya cases")
    for i, (prompt, expected) in enumerate(LAYA_CASES, 1):
        code, _, _ = run({"hook_event_name": "UserPromptSubmit", "prompt": prompt})
        check(f"laya case {i}: exit {expected} (got {code})", code == expected)
else:
    print(f"Laya server down: SKIP {len(LAYA_CASES)} Laya cases (regex only, proves fail-open)")

# --- masking round trip ---
if os.path.exists(MAP_PATH):
    os.remove(MAP_PATH)

code, _, err = run({"hook_event_name": "UserPromptSubmit", "prompt": "email pat@example.com about SSN 123-45-6789"})
masked = err.strip().splitlines()[-1]
check("prompt with PHI is blocked", code == 2)
check("block message holds masked prompt", masked == f"email {tok('EMAIL', 1)} about SSN {tok('SSN', 1)}")
check("original values absent from block message", "pat@example.com" not in err and "123-45-6789" not in err)
check("map stored outside project", json.load(open(MAP_PATH))["tokens"] == {tok("EMAIL", 1): "pat@example.com", tok("SSN", 1): "123-45-6789"})

code, _, err = run({"hook_event_name": "UserPromptSubmit", "prompt": "cc pat@example.com and bob@example.com"})
check("same value reuses its token", err.strip().splitlines()[-1] == f"cc {tok('EMAIL', 1)} and {tok('EMAIL', 2)}")

code, out, _ = run({"hook_event_name": "UserPromptSubmit", "prompt": masked})
check("masked prompt passes the regex", code == 0)
check("masked prompt gets placeholder instructions", "additionalContext" in (out or {}).get("hookSpecificOutput", {}))

for label in ["EMAIL", "SSN", "PHONE", "CARD", "URL", "IP", "VIN"]:
    code, _, _ = run({"hook_event_name": "UserPromptSubmit", "prompt": f"value {tok(label, 12)} here"})
    check(f"{label} token never trips a pattern", code == 0)

# --- PreToolUse: tokens -> real values in nested tool input ---
code, out, _ = run({"hook_event_name": "PreToolUse", "tool_name": "Write", "tool_input": {
    "file_path": "C:\\x\\notes.txt", "old_string": f"to: {tok('EMAIL', 1)}", "new_string": f"to: {tok('EMAIL', 1)}\nssn: {tok('SSN', 1)}",
    "edits": [{"s": tok("UNKNOWN", 9)}]}})
upd = (out or {}).get("hookSpecificOutput", {}).get("updatedInput", {})
check("PreToolUse unmasks old_string", upd.get("old_string") == "to: pat@example.com")
check("PreToolUse unmasks new_string", upd.get("new_string") == "to: pat@example.com\nssn: 123-45-6789")
check("PreToolUse keeps unknown tokens and other fields", upd.get("edits") == [{"s": tok("UNKNOWN", 9)}] and upd.get("file_path") == "C:\\x\\notes.txt")
check("PreToolUse never auto-allows", "permissionDecision" not in (out or {}).get("hookSpecificOutput", {}))
code, out, _ = run({"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "ls"}})
check("PreToolUse silent without tokens", code == 0 and out is None)

# --- PostToolUse: known + new values -> tokens, shape preserved ---
resp = {"stdout": "pat@example.com, 123-45-6789, new: 10.1.2.3", "stderr": "", "interrupted": False, "isImage": False}
code, out, _ = run({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}, "tool_response": resp})
upd = (out or {}).get("hookSpecificOutput", {}).get("updatedToolOutput", {})
check("PostToolUse re-masks known and new values", upd.get("stdout") == f"{tok('EMAIL', 1)}, {tok('SSN', 1)}, new: {tok('IP', 1)}")
check("PostToolUse keeps output shape", set(upd) == set(resp) and upd["interrupted"] is False)
check("PostToolUse tells Claude to use Write, not Edit", "Write tool" in out["hookSpecificOutput"].get("additionalContext", ""))
check("new value added to map", json.load(open(MAP_PATH))["tokens"].get(tok("IP", 1)) == "10.1.2.3")
code, out, _ = run({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {}, "tool_response": {"stdout": "ok"}})
check("PostToolUse silent when clean", code == 0 and out is None)

# --- MessageDisplay: tokens -> real values on screen ---
code, out, _ = run({"hook_event_name": "MessageDisplay", "delta": f"Wrote {tok('EMAIL', 1)} to notes.txt\n", "index": 0, "final": True})
check("MessageDisplay unmasks", (out or {}).get("hookSpecificOutput", {}).get("displayContent") == "Wrote pat@example.com to notes.txt\n")

# --- SessionEnd: map deleted ---
run({"hook_event_name": "SessionEnd", "reason": "other"})
check("SessionEnd deletes map", not os.path.exists(MAP_PATH))

sys.exit(1 if failed else 0)
