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
    print(("PASS" if ok else "FAIL"), ascii(prompt), "expected", expected, "got", r.returncode)
    failed += not ok

sys.exit(1 if failed else 0)
