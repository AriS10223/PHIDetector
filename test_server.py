"""Exercise the local Safe Harbor server. Stdlib only.

Run with server.py up on 127.0.0.1:8420:
    python3 test_server.py

Prints category / phi_prob / blocked per case, plus warm latency for a
1-chunk and an 8-chunk (worst-case) request. Exits nonzero if ANY negative case is blocked:
a missed positive is acceptable for the demo, blocking an ordinary coding prompt is not.
"""
import json
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8420"

# Things the regex hook can't catch and Laya should: names, addresses, individual-tied dates, free-form IDs.
POSITIVES = [
    "send this to Sarah Chen",
    "she was born March 3rd 1961",
    "lives at 42 Elm Street, Springfield",
    "MRN 00482913",
    "patient John Alvarez was admitted on 2024-02-14 and discharged the 19th",
    "the member's health plan ID is BCBS-77120394",
    "Mr. Okafor, age 92, lives at 118 Harbor View Rd, Portland ME 04101",
    "device serial SN-4471-XK99 was implanted last spring",
]
# Ordinary coding prompts. Every one of these MUST come back blocked=false.
NEGATIVES = [
    "fix the off-by-one in getUserName",
    "schedule the deploy for Friday",
    "add a zip code field to the form",
    "help me refactor this function",
    "bump version to 3.14.159",
    "write a unit test for the date parser",
    "why does my React useEffect run twice",
    "rename the patient table to encounters in the migration",
    "explain the difference between a list and a tuple in Python",
    "the HIPAA Safe Harbor standard lists 18 identifier categories",
]


def check(text, timeout=30):
    req = urllib.request.Request(
        f"{BASE}/check",
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def main():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"{BASE}/health", timeout=5) as resp:
        health = json.loads(resp.read())
    print("health:", health)
    assert health.get("status") == "ok"

    failed_negatives = 0
    missed_positives = 0
    for label, cases in [("POSITIVE", POSITIVES), ("NEGATIVE", NEGATIVES)]:
        print(f"\n== {label} ==")
        for text in cases:
            r = check(text)
            mark = "  "
            if label == "NEGATIVE" and r["blocked"]:
                mark = "!!"
                failed_negatives += 1
            if label == "POSITIVE" and not r["blocked"]:
                mark = "--"
                missed_positives += 1
            print(f"{mark} blocked={str(r['blocked']):5} cat={r['category']:10} "
                  f"phi={r['phi_prob']:.3f} "
                  f"top={r.get('top_category', '?'):10} {text!r}")

    # Latency, warm. 1 chunk, then a prompt long enough to hit MAX_CHUNKS (server caps it).
    print("\n== latency (warm) ==")
    one = "help me refactor this function"
    eight = ("def handler(event, ctx):\n    return {'ok': True}\n" * 200)[:6000]
    for name, text in [("1-chunk", one), ("max-chunk", eight)]:
        check(text)  # warm
        t = time.time()
        r = check(text)
        dt = time.time() - t
        print(f"{name}: {dt:.2f}s  (chunks={r.get('chunks')}, blocked={r['blocked']})")

    print(f"\nnegatives wrongly blocked: {failed_negatives}/{len(NEGATIVES)}   "
          f"positives missed: {missed_positives}/{len(POSITIVES)}")
    sys.exit(1 if failed_negatives else 0)


if __name__ == "__main__":
    main()
