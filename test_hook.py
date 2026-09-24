import json, os, subprocess, sys, urllib.request

HOOK = ".claude/hooks/pii_check.py"

CASES = [
    ("help me refactor this function", 0),
    ("bump version to 3.14.159", 0),
    ("run it on 127.0.0.1:8420", 0),
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
    ("his birthday is 1/1/1980", 2),
    ("admitted 2024-03-03", 2),
    ("she was born March 3rd 1961", 2),
    ("release is scheduled for 10/1", 0),
]

# Second layer: only meaningful when server.py is up on 127.0.0.1:8420. Regex can't catch these.
# With the server stopped they are skipped, and the regex rows above must still all PASS (fail-open).
LAYA_CASES = [
    ("send this to Sarah Chen", 2),
    ("Sarah Chen has diabetes, write her a care plan", 2),
    ("Mr. Okafor, age 92, was discharged yesterday", 2),
    ("lives at 42 Elm Street, Springfield", 2),
    ("MRN 00482913", 2),
    ("add a zip code field to the form", 0),
    ("write a unit test for the date parser", 0),
    ("rename the patient table to encounters in the migration", 0),
]

def laya_up():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://127.0.0.1:8420/health", timeout=2) as resp:
            return json.loads(resp.read()).get("status") == "ok"
    except Exception:
        return False

if laya_up():
    print("Laya server up: including", len(LAYA_CASES), "Laya rows")
    CASES = CASES + LAYA_CASES
else:
    print("Laya server DOWN: skipping Laya rows (regex rows must still pass -> proves fail-open)")

# Redaction contract: for every regex-blocked row, the block message must show the masked prompt
# and must NOT echo the identifier itself back to the terminal. (Laya rows have no span to mask.)
REDACT_ROWS = {
    "patient ssn 123-45-6789": "123-45-6789",
    "vehicle VIN is 1HGCM82633A004352": "1HGCM82633A004352",
    "the server is at 192.168.1.10": "192.168.1.10",
    "call me at (555) 123-4567": "123-4567",
    "call me at 555.123.4567": "555.123.4567",
    "email me at pat@example.com": "pat@example.com",
    "see https://example.com/patient/42": "example.com",
    "his birthday is 1/1/1980": "1/1/1980",
    "admitted 2024-03-03": "2024-03-03",
    "she was born March 3rd 1961": "March 3rd 1961",
}

failed = 0
for prompt, expected in CASES:
    payload = json.dumps({"prompt": prompt}, ensure_ascii=False).encode("utf-8")
    r = subprocess.run([sys.executable, HOOK], input=payload, capture_output=True, timeout=15,
                       env={**os.environ, "PATH": "/nonexistent"})  # no pbcopy: exercises the fallback hint
    ok = r.returncode == expected
    note = ""
    if prompt in REDACT_ROWS:
        err = r.stderr.decode("utf-8", errors="replace")
        leaked = REDACT_ROWS[prompt] in err
        masked = "***" in err
        ok = ok and masked and not leaked
        note = f" redacted={masked} leaked={leaked}"
    print(("PASS" if ok else "FAIL"), ascii(prompt), "expected", expected, "got", r.returncode + 0, note)
    failed += not ok

# Unit checks on redact() itself (import by path: the hooks dir is not a package).
import importlib.util
spec = importlib.util.spec_from_file_location("pii_check", HOOK)
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
UNIT = [
    ("Email pat@example.com about SSN 123-45-6789", "Email *** about SSN ***", {"email": 1, "ssn": 1}),
    ("see https://ex.com/u/pat@example.com now", "see *** now", {"url": 1}),   # email inside URL: one mask
    ("bump version to 3.14.159", "bump version to 3.14.159", {}),
    ("card 4111 1111 1111 1111 on file", "card *** on file", {"account_or_card_number": 1}),
    ("born 01-01-80, seen Jan 5, 2020 and 3 March 1961", "born ***, seen *** and ***", {"date": 3}),
    ("deadline 10/1, version 3.14.159, host 127.0.0.1:8420", "deadline 10/1, version 3.14.159, host 127.0.0.1:8420", {}),
]
for text, want, want_counts in UNIT:
    got, counts = mod.redact(text)
    ok = got == want and counts == want_counts
    print(("PASS" if ok else "FAIL"), "redact", ascii(text), "->", ascii(got), counts)
    failed += not ok

sys.exit(1 if failed else 0)
