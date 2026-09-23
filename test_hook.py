import json, subprocess, sys, urllib.request

HOOK = ".claude/hooks/pii_check.py"

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

# End-to-end through Laya: regex can't see any of these. Only run when server.py is up.
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


cases = CASES
if laya_up():
    print("Laya server up: running regex + Laya cases")
    cases = CASES + LAYA_CASES
else:
    print(f"Laya server down: SKIP {len(LAYA_CASES)} Laya cases (regex only, proves fail-open)")

failed = 0
for prompt, expected in cases:
    payload = json.dumps({"prompt": prompt}, ensure_ascii=False).encode("utf-8")
    r = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, timeout=15)
    ok = r.returncode == expected
    print(("PASS" if ok else "FAIL"), ascii(prompt), "expected", expected, "got", r.returncode,
          r.stderr.decode(errors="replace").strip())
    failed += not ok

sys.exit(1 if failed else 0)
